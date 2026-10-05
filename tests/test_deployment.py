"""The deployment artifacts.

There are now three places that describe how this app starts -- the Procfile,
the Dockerfile and render.yaml -- and the failure mode when they disagree is
that a platform quietly runs a command nobody has ever tested. Two of them
carrying the same gunicorn invocation is only safe if something checks they
still match.

CI goes further and actually builds the image and talks to it; these are the
cheap checks that run everywhere, including on a machine with no Docker.
"""

import pathlib
import re

import pytest

PROCFILE = 'Procfile'
DOCKERFILE = 'Dockerfile'
DOCKERIGNORE = '.dockerignore'
RENDER = 'render.yaml'


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


@pytest.fixture
def procfile_web():
    for line in read(PROCFILE).splitlines():
        if line.startswith('web:'):
            return line.split('web:', 1)[1].strip()
    pytest.fail('Procfile has no web: process')


@pytest.fixture
def dockerfile_cmd():
    """The shell command out of the Dockerfile's exec-form CMD."""
    cmd = re.search(r'^CMD \[(.*)\]\s*$', read(DOCKERFILE), re.M)
    assert cmd, 'Dockerfile has no exec-form CMD'
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', cmd.group(1))
    assert parts[:2] == ['sh', '-c'], f'expected sh -c form, got {parts[:2]}'
    return parts[2]


def test_the_container_starts_the_same_command_as_the_procfile(procfile_web, dockerfile_cmd):
    """Drift here means Render runs one thing and a Procfile host runs another."""
    assert dockerfile_cmd == procfile_web, (
        'Dockerfile CMD and Procfile web have diverged:\n'
        f'  Procfile:   {procfile_web}\n'
        f'  Dockerfile: {dockerfile_cmd}'
    )


def test_the_start_command_still_binds_one_worker(procfile_web):
    """Socket.IO sessions live in the worker process. A second worker turns that
    into intermittent message loss under load -- see docs/DEPLOYMENT.md."""
    assert '--workers 1' in procfile_web
    assert '--worker-class gthread' in procfile_web


def test_the_start_command_honours_the_platform_port(procfile_web):
    """Every PaaS assigns the port; a hard-coded 5000 is never reachable."""
    assert '${PORT:-5000}' in procfile_web


# ----------------------------------------------------------------------
# The image must not carry local state or credentials
# ----------------------------------------------------------------------

@pytest.mark.parametrize('pattern, why', [
    ('app/data', 'a dev database baked into the image ships real accounts'),
    ('app/shares', 'local share images do not belong in a registry'),
    ('.env', 'environment files carry secrets'),
    ('serviceAccountKey*.json', 'Firebase service-account key'),
    ('firebase-adminsdk*.json', 'Firebase service-account key'),
    ('run-firebase.ps1', 'local launcher carrying the web config'),
    ('.git', 'the whole history, including anything ever committed'),
])
def test_dockerignore_keeps_local_state_out_of_the_image(pattern, why):
    assert pattern in read(DOCKERIGNORE).splitlines(), f'.dockerignore is missing {pattern}: {why}'


def test_the_image_installs_only_runtime_dependencies():
    """requirements-dev pulls in pytest and the ONNX export toolchain. If
    production needs them, something is wrong with the split."""
    # Comments stripped: the Dockerfile explains *why* requirements-dev is
    # absent, and matching that prose would be checking the documentation
    # rather than the build.
    instructions = [
        line for line in read(DOCKERFILE).splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert any('requirements.txt' in line for line in instructions)
    assert not any('requirements-dev' in line for line in instructions), \
        'the image installs the dev/test toolchain'


def test_the_image_does_not_run_as_root():
    assert re.search(r'^USER\s+(?!root)\S+', read(DOCKERFILE), re.M), \
        'Dockerfile should drop to a non-root user'


def test_no_partial_dependency_manifest_shadows_requirements():
    """A pyproject.toml is fine; one that under-declares is not.

    The repo briefly carried one listing a single dependency against the
    fifteen in requirements.txt. Nothing in this project read it, but build
    tools and PaaS buildpacks prefer pyproject.toml when it exists -- so the
    app would have installed Flask alone and died on its first import, with
    requirements.txt sitting right there looking correct.
    """
    pyproject = pathlib.Path('pyproject.toml')
    if not pyproject.exists():
        return

    import tomllib
    declared = tomllib.loads(pyproject.read_text(encoding='utf-8')) \
        .get('project', {}).get('dependencies')
    if declared is None:
        return                      # tooling config only, not a dependency list

    def name_of(spec):
        return re.split(r'[\[<>=!~;]', spec, 1)[0].strip().lower()

    required = {
        name_of(line) for line in read('requirements.txt').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    }
    missing = sorted(required - {name_of(d) for d in declared})
    assert not missing, (
        'pyproject.toml declares dependencies but omits '
        f'{len(missing)} from requirements.txt: {missing}'
    )


def test_opencv_and_sklearn_system_libraries_are_installed():
    """opencv-python-headless still links libglib, and scikit-learn needs
    libgomp. Both fail at import time, so the image builds clean and the first
    request is a 500."""
    dockerfile = read(DOCKERFILE)
    for lib in ('libglib2.0-0', 'libgomp1'):
        assert lib in dockerfile, f'{lib} is needed at runtime'


# ----------------------------------------------------------------------
# The Render blueprint
# ----------------------------------------------------------------------

def test_render_blueprint_points_at_the_dockerfile():
    render = read(RENDER)
    assert 'dockerfilePath: ./Dockerfile' in render
    assert 'runtime: docker' in render


def test_render_generates_a_persistent_secret_key():
    """Unset, the app mints a random key per boot and every session dies on a
    restart -- which on a free plan happens often."""
    render = read(RENDER)
    assert re.search(r'key: REVEAL_X_SECRET_KEY\s+generateValue: true', render)


def test_render_uses_a_database_that_survives_a_deploy():
    """The container filesystem is wiped on every deploy; on SQLite that means
    every account vanishes when you push."""
    assert 'fromDatabase' in read(RENDER)


def test_render_trusts_the_proxy_for_throttling():
    """Render is a reverse proxy. Without this every request looks like it came
    from the proxy, so one failed password throttles everyone."""
    assert re.search(r'key: REVEALX_TRUST_PROXY\s+value: "true"', read(RENDER))


def test_render_blueprint_carries_no_secrets():
    """Credentials must be `sync: false` (set in the dashboard), never values."""
    render = read(RENDER)
    for key in ('REVEALX_ADMIN_PASSWORD', 'FIREBASE_API_KEY', 'FIREBASE_CREDENTIALS_JSON'):
        block = re.search(rf'key: {key}\s+(\S+)', render)
        assert block, f'{key} missing from the blueprint'
        assert block.group(1).startswith('sync:'), f'{key} must be sync: false, not a literal value'
    assert 'AIza' not in render, 'a Firebase API key is hard-coded in the blueprint'


def test_render_health_check_hits_an_endpoint_that_always_answers():
    render = read(RENDER)
    path = re.search(r'healthCheckPath: (\S+)', render)
    assert path, 'no healthCheckPath'
    # "/" renders a template; /api/auth/config is a plain JSON 200 whether or
    # not Firebase is configured, which is what a health check wants.
    assert path.group(1) == '/api/auth/config'
