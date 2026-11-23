#!/usr/bin/env python3
"""Prepare and start a fresh Matrix LAN Docker Compose installation."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'services'
TEMPLATES = ROOT / 'templates'
CORE = ('db', 'synapse', 'element', 'caddy', 'authorization')
GO_BRIDGES = ('whatsapp', 'telegram', 'facebook', 'instagram', 'twitter', 'tiktok', 'linkedin', 'email')
SOCIAL = ('threads', 'ok', 'vk')
SHOPPING = ('taobao', 'pinduoduo')
REGISTRATION_FOR = {
    'matrix-imo': 'matrix-imo-registration.yaml',
    'matrix-wechat': 'matrix-wechat-registration.yaml',
    **{f'mautrix-{key}': f'mautrix-{key}-registration.yaml' for key in GO_BRIDGES},
    **{f'matrix-{key}': f'matrix-{key}-registration.yaml' for key in SHOPPING},
    **{f'matrix-{key}': f'appservice-{key}-20261119.yaml' for key in SOCIAL},
}
POSTGRES_ROLES = ('synapse', 'whatsapp', 'telegram', 'linkedin', 'authorization')
SECRET_PATTERN = re.compile(r'__SECRET_[A-Z0-9_]+__')
SOURCE_TOKEN = re.compile(r'__(?:MATRIX_OWNER|PUBLIC_ORIGIN|PUBLIC_ORIGIN_RE|SERVER_NAME|SERVER_RE)__')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value, uid=0, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    temp.write_text(value, encoding='utf-8', newline='\n')
    if os.name != 'nt':
        temp.chmod(mode)
        if os.geteuid() == 0:
            os.chown(temp, uid, uid)
    temp.replace(path)


def state_directory(path, uid=0):
    path.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        path.chmod(0o700)
        if os.geteuid() == 0:
            os.chown(path, uid, uid)


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def compose(runtime, *args, **kwargs):
    return command(['docker', 'compose', '--project-directory', str(runtime),
                    '-f', str(runtime / 'compose.yaml'), *args], **kwargs)


def credentials(path):
    if not path:
        return None
    data = load(path)
    if type(data.get('api_id')) is not int or data['api_id'] <= 0 or not re.fullmatch(r'[a-fA-F0-9]{32}', str(data.get('api_hash', ''))):
        raise ValueError('Telegram configuration requires a positive api_id and a 32-digit hexadecimal api_hash.')
    return data


def bridge_catalog(runtime, settings):
    """Report every bundled connector without treating an unconfigured one as installed."""
    catalog = load(TEMPLATES / 'bridge-catalog.json')
    configured = set(settings['services'])
    entries = []
    for bridge in catalog['bridges']:
        service = bridge.get('service')
        available = bridge.get('available', True)
        prepared = bool(service and service in configured)
        entries.append({
            'id': bridge['id'],
            'label': bridge['label'],
            'service': service,
            'available': available,
            'prepared': prepared,
            'status': 'unavailable' if not available else ('prepared' if prepared else 'not_prepared'),
            'setup': bridge['setup'],
        })
    return {'schema_version': 1, 'bridges': entries}


def choose_setup(args):
    if not re.fullmatch(r'[a-z0-9._=+\-/]{1,48}', args.owner):
        raise ValueError('Use a lowercase Matrix localpart for --owner.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,45}', args.project):
        raise ValueError('Invalid Docker Compose project name.')
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', args.host) or len(args.host) > 253 or '..' in args.host:
        raise ValueError('--host must be a DNS name or IPv4 address without a port.')
    ipaddress.IPv4Address(args.bind)
    if not 1 <= args.port <= 65535:
        raise ValueError('Invalid HTTPS port.')
    if args.cbmrs_origin:
        cbmrs = urlsplit(args.cbmrs_origin)
        if cbmrs.scheme != 'https' or not cbmrs.hostname or cbmrs.username or cbmrs.password or cbmrs.path or cbmrs.query or cbmrs.fragment:
            raise ValueError('--cbmrs-origin must be an HTTPS origin, without a path or credentials.')
    telegram = credentials(args.telegram_config)
    env = load(TEMPLATES / 'services.json')
    extra = [service for service in env if service not in CORE and (service != 'mautrix-telegram' or telegram)]
    selected = list(CORE) + ([] if args.core else extra)
    secret_keys = set()
    for template in TEMPLATES.rglob('*'):
        if template.is_file():
            secret_keys.update(SECRET_PATTERN.findall(template.read_text(encoding='utf-8')))
    secret_keys.update({'__SECRET_API_BEARER__', '__SECRET_WECHAT_NATIVE__', '__SECRET_POSTGRES__',
                        *(f'__SECRET_DB_{role.upper()}__' for role in POSTGRES_ROLES)})
    return {'host': args.host, 'bind': args.bind, 'port': args.port, 'project': args.project,
            'owner': args.owner, 'services': selected, 'telegram': telegram, 'cbmrs_origin': args.cbmrs_origin,
            'password': secrets.token_urlsafe(27), 'owner_token': '',
            'secrets': {key: secrets.token_hex(32) for key in sorted(secret_keys)}}


def replacements(settings):
    host = settings['host']
    origin = f'https://{host}:{settings["port"]}'
    out = dict(settings['secrets'])
    out.update({'__PUBLIC_ORIGIN__': origin, '__SERVER_NAME__': host,
                '__PUBLIC_ORIGIN_RE__': re.escape(origin),
                '__MATRIX_OWNER__': f'@{settings["owner"]}:{host}',
                '__SERVER_RE__': re.escape(host), '__OWNER_ACCESS_TOKEN__': settings['owner_token'],
                '__TELEGRAM_API_HASH__': (settings['telegram'] or {}).get('api_hash', ''),
                '__IMO_AS_TOKEN_HASH__': hashlib.sha256(out['__SECRET_MATRIX_IMO_AS_TOKEN__'].encode()).hexdigest()})
    return out


def render(value, tokens):
    if isinstance(value, dict):
        return {render(key, tokens): render(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, tokens) for item in value]
    if isinstance(value, str):
        for key, item in tokens.items():
            value = value.replace(key, item)
    return value


def build_contexts(runtime, tokens):
    dest = runtime / 'build'
    # Build contexts contain application code only. The private state tree is a sibling.
    replacements_for_code = {key: value for key, value in tokens.items() if SOURCE_TOKEN.fullmatch(key)}
    shutil.copytree(SOURCE, dest, dirs_exist_ok=False)
    for path in dest.rglob('*'):
        if not path.is_file() or path.suffix not in ('.py', '.js', '.css', '.html', '.svg'):
            continue
        text = path.read_text(encoding='utf-8')
        if SOURCE_TOKEN.search(text):
            path.write_text(render(text, replacements_for_code), encoding='utf-8', newline='\n')


def configure(runtime, settings):
    tokens = replacements(settings)
    state = runtime / 'state'
    registrations = {}
    for srv, name in REGISTRATION_FOR.items():
        if srv in settings['services']:
            registrations[srv] = (name, render(load(TEMPLATES / 'registrations' / (name + '.json')), tokens))
    config = {p.stem: render(load(p), tokens) for p in (TEMPLATES / 'config').glob('*.json')}
    config['telegram']['network']['api_id'] = (settings['telegram'] or {}).get('api_id', 0)

    synapse = state / 'synapse'
    state_directory(synapse, 991)
    config['synapse']['app_service_config_files'] = ['/data/' + name for name, _ in registrations.values()]
    config['synapse']['public_baseurl'] = tokens['__PUBLIC_ORIGIN__'] + '/'
    config['synapse']['database']['args'].pop('database', None)
    config['synapse']['modules'] = [mod for mod in config['synapse'].get('modules', [])
                                    if mod.get('module') != 'history_cutoff.HistoryCutoff']
    config['synapse']['suppress_key_server_warning'] = True
    config['synapse']['password_config'] = {'enabled': True, 'localdb_enabled': True}
    write(synapse / 'homeserver.yaml', config['synapse'], 991)
    write(synapse / 'log.yaml', {'version': 1, 'formatters': {'standard': {'format': '%(levelname)s %(name)s: %(message)s'}},
                                'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'}},
                                'root': {'level': 'WARNING', 'handlers': ['console']}, 'disable_existing_loggers': False}, 991)
    for name, registration in registrations.values():
        write(synapse / name, registration, 991)

    for bridge in GO_BRIDGES:
        srv = f'mautrix-{bridge}'
        if srv not in registrations:
            continue
        uid = 10001 if bridge == 'linkedin' else 65532 if bridge == 'email' else 1337
        directory = state / bridge
        state_directory(directory, uid)
        write(directory / 'config.yaml', config[bridge], uid)
        write(directory / 'registration.yaml', registrations[srv][1], uid)
    for name in (*SOCIAL, *SHOPPING):
        srv = 'matrix-' + name
        if srv not in registrations:
            continue
        directory = state / name
        state_directory(directory, 10001)
        write(directory / 'matrix.json', {k: registrations[srv][1][k] for k in ('as_token', 'hs_token')}, 10001)
    for profile in ('social', 'shopping'):
        write(state / (profile + '-owner.json'), config[profile + '-owner'], 10001)
    for name, uid in (('accounts', 10001), ('imo', 0), ('wechat', 10001)):
        if 'matrix-' + name in settings['services']:
            directory = state / name
            state_directory(directory, uid)
            write(directory / 'config.json', config[name], uid)
    if 'wechat-native' in settings['services']:
        for directory in ('wechat-native', 'wechat-cache'):
            state_directory(state / directory, 10001)
        write(state / 'wechat-token', tokens['__SECRET_WECHAT_NATIVE__'] + '\n', 10001)
        write(state / 'wechat-native.toml', (TEMPLATES / 'wechat-native.toml').read_text(encoding='utf-8'), 10001)

    write(state / 'element.json', config['element'], mode=0o644)
    caddy = (TEMPLATES / 'Caddyfile').read_text(encoding='utf-8')
    caddy = caddy.replace('^https://__SERVER_RE__:(18081|18008)$', '^' + re.escape(tokens['__PUBLIC_ORIGIN__']) + '$')
    write(state / 'Caddyfile', render(caddy, tokens), mode=0o644)

    pg_init = []
    for role in POSTGRES_ROLES:
        secret = tokens[f'__SECRET_DB_{role.upper()}__']
        pg_init.append(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{secret}';")
        pg_init.append(f"CREATE DATABASE \"{role}\" OWNER \"{role}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C';")
    pg_init.append((SOURCE / 'authorization' / 'schema.sql').read_text(encoding='utf-8'))
    pg_init.append('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "authorization";')
    pg_init.append('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO authorization;')
    write(state / 'postgres-init.sql', '\n'.join(pg_init) + '\n', 70, 0o640)
    write(state / 'authorization.env', '\n'.join((
        'MATRIX_LAN_DSN=postgresql://authorization:' + tokens['__SECRET_DB_AUTHORIZATION__'] + '@db:5432/postgres',
        'SYNAPSE_REGISTRATION_SECRET=' + tokens['__SECRET_SYNAPSE_REGISTRATION_SHARED_SECRET__'],
        'MATRIX_SERVER_NAME=' + settings['host'],
        'SYNAPSE_URL=http://synapse:8008',
    )) + '\n')

    api_env = load(TEMPLATES / 'chat-api-env.json')
    api_env.update({'MATRIX_USER_ID': tokens['__MATRIX_OWNER__'], 'MATRIX_ACCESS_TOKEN': settings['owner_token'],
                    'MATRIX_BASE_URL': 'http://synapse:8008', 'MATRIX_HISTORY_CUTOFF_MS': '0',
                    'API_BEARER_TOKEN': tokens['__SECRET_API_BEARER__'],
                    'WHATSAPP_PROVISIONING_SECRET': config['whatsapp']['provisioning']['shared_secret'],
                    'TELEGRAM_PROVISIONING_SECRET': config['telegram']['provisioning']['shared_secret']})
    write(state / 'chat-api.env', '\n'.join(f'{k}={v}' for k, v in api_env.items()) + '\n')
    write(runtime / 'compose.yaml', compose_spec(settings, tokens))


def compose_spec(settings, tokens):
    all_services = render(load(TEMPLATES / 'services.json'), tokens)
    services = {key: all_services[key] for key in settings['services']}
    for name, srv in services.items():
        srv['restart'] = 'unless-stopped'
        if 'depends_on' in srv:
            dependencies = srv['depends_on']
            srv['depends_on'] = {dep: spec for dep, spec in dependencies.items() if dep in services} if isinstance(dependencies, dict) else [dep for dep in dependencies if dep in services]
        if name in ('element', 'matrix-accounts', 'matrix-imo', 'matrix-wechat', 'chat-api', 'mautrix-facebook', 'mautrix-email', 'mautrix-tiktok', *('matrix-' + x for x in (*SOCIAL, *SHOPPING))):
            context = name.removeprefix('matrix-').removeprefix('mautrix-')
            if context in SHOPPING: context = 'shopping'
            if context in ('ok', 'vk'): context = 'social'
            srv['image'] = f'{settings["project"]}-{context}:local'
            srv['build'] = './build/' + context
    services['db']['environment'] = {'POSTGRES_USER': 'postgres', 'POSTGRES_PASSWORD': tokens['__SECRET_POSTGRES__'], 'POSTGRES_DB': 'postgres'}
    services['db']['volumes'] = ['postgres:/var/lib/postgresql/data', './state/postgres-init.sql:/docker-entrypoint-initdb.d/10-matrix.sql:ro']
    services['db']['healthcheck']['test'] = ['CMD', 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'postgres']
    services['synapse']['environment'].update({'UID': '991', 'GID': '991'})
    services['synapse']['volumes'] = ['./state/synapse:/data', './build/synapse:/data/custom_modules:ro']
    services['element']['volumes'] = ['./state/element.json:/app/config.json:ro']
    services['element']['environment'] = {'ELEMENT_WEB_PORT': '80'}
    if 'authorization' in services:
        services['caddy'].setdefault('depends_on', []).append('authorization')
    services['caddy']['ports'] = [f'{settings["bind"]}:{settings["port"]}:18443']
    services['caddy']['volumes'] = ['./state/Caddyfile:/etc/caddy/Caddyfile:ro', 'caddy_data:/data', 'caddy_config:/config']
    for bridge in GO_BRIDGES:
        name = 'mautrix-' + bridge
        if name in services:
            services[name]['volumes'] = [f'./state/{bridge}:/data']
    if 'mautrix-twitter' in services:
        services['mautrix-twitter']['image'] = 'dock.mau.dev/mautrix/twitter@sha256:0276d454df0bed3713610a8c6eef1470245f531dd2aee35ad6084dd0332621b7'
    for bridge in ('accounts', 'imo'):
        name = 'matrix-' + bridge
        if name in services: services[name]['volumes'] = [f'./state/{bridge}:/data']
    if 'matrix-accounts' in services and settings.get('cbmrs_origin'):
        services['matrix-accounts'].setdefault('environment', {})['MSB_CBMRS_ORIGIN'] = settings['cbmrs_origin']
    if 'chat-api' in services:
        services['chat-api']['env_file'] = ['./state/chat-api.env']
        services['chat-api'].pop('environment', None)
    for family, bridges in [('social', SOCIAL), ('shopping', SHOPPING)]:
        for bridge in bridges:
            name = 'matrix-' + bridge
            if name in services:
                services[name]['volumes'] = [f'./state/{bridge}:/data', f'./state/{family}-owner.json:/run/matrix-owner.json:ro']
    if 'matrix-wechat' in services:
        services['matrix-wechat']['network_mode'] = 'service:wechat-native'
        services['matrix-wechat']['volumes'] = ['./state/wechat:/wechat-data', './state/wechat-token:/run/secrets/wechat-api-token:ro']
        services['wechat-native']['volumes'] = ['./state/wechat-native:/var/lib/agent-wechat', './state/wechat-cache:/var/cache/agent-wechat',
            './state/wechat-native.toml:/etc/agent-wechat/server/config.toml:ro', './state/wechat-token:/run/secrets/wechat-api-token:ro']
    return {'name': settings['project'], 'services': services, 'volumes': {k: {} for k in ('postgres','caddy_data','caddy_config')}}


BOOTSTRAP = '''import hashlib,hmac,json,sys,urllib.request,urllib.error
user,password,secret=json.load(sys.stdin)
base="http://127.0.0.1:8008"
def api(path, body=None):
    req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=8) as res:return json.load(res)
login={"type":"m.login.password","identifier":{"type":"m.id.user","user":user},"password":password,"initial_device_display_name":"Matrix LAN integration"}
try:
    result=api("/_matrix/client/v3/login",login)
except urllib.error.HTTPError as exc:
    if exc.code!=403:raise
    path="/_synapse/admin/v1/register"
    nonce=api(path)["nonce"]
    mac=hmac.new(secret.encode(),"\\0".join((nonce,user,password,"admin")).encode(),hashlib.sha1).hexdigest()
    result=api(path,{"nonce":nonce,"username":user,"password":password,"admin":True,"mac":mac})
print(json.dumps({"access_token":result["access_token"]}))
'''


def start(runtime, settings, auth_file):
    """Start infrastructure without manufacturing a Matrix user.

    Registration is intentionally disabled in Synapse until the invitation
    registration backend is wired into the web UI. Do not create an owner here.
    """
    compose(runtime, 'config', '--quiet')
    compose(runtime, 'up', '-d', 'db', 'synapse', 'element', 'caddy', 'authorization')
    print(f'Open https://{settings["host"]}:{settings["port"]}/')
    print('Authorization API started; web UI and failure recovery are not yet ready.')
    print('No Matrix user or superuser has been created.')



def enable_bridge(runtime, settings, auth_file, bridge, config_path=None):
    """Add one bridge to an existing installation without re-preparing runtime."""
    if not settings['owner_token']:
        raise ValueError('Start Matrix LAN and initialize the owner before adding bridges.')
    if bridge not in ('telegram', 'whatsapp'):
        raise ValueError('Supported bridges: telegram, whatsapp.')
    service = 'mautrix-' + bridge
    if service in settings['services']:
        raise ValueError(f'{bridge} is already configured.')
    if bridge == 'telegram':
        if config_path is None:
            raise ValueError('Telegram requires a JSON credentials file with api_id and api_hash.')
        settings['telegram'] = credentials(config_path)

    tokens = replacements(settings)
    state = runtime / 'state'
    name = REGISTRATION_FOR[service]
    registration = render(load(TEMPLATES / 'registrations' / (name + '.json')), tokens)
    config = render(load(TEMPLATES / 'config' / (bridge + '.json')), tokens)
    if bridge == 'telegram':
        config['network']['api_id'] = settings['telegram']['api_id']

    # Write bridge configuration before registering it with Synapse.
    state_directory(state / bridge, 1337)
    write(state / bridge / 'config.yaml', config, 1337)
    write(state / bridge / 'registration.yaml', registration, 1337)
    write(state / 'synapse' / name, registration, 991)

    homeserver_path = state / 'synapse' / 'homeserver.yaml'
    homeserver = load(homeserver_path)
    appservice_path = '/data/' + name
    if appservice_path not in homeserver['app_service_config_files']:
        homeserver['app_service_config_files'].append(appservice_path)

    updated_settings = dict(settings)
    updated_settings['services'] = [*settings['services'], service]
    updated_compose = compose_spec(updated_settings, tokens)
    # Validate candidate Compose config before changing the running installation.
    with tempfile.TemporaryDirectory(prefix='bridge-check-', dir=runtime) as temp:
        candidate = Path(temp) / 'compose.yaml'
        write(candidate, updated_compose)
        command(['docker', 'compose', '--project-directory', str(runtime),
                 '-f', str(candidate), 'config', '--quiet'])

    write(homeserver_path, homeserver, 991)
    write(runtime / 'compose.yaml', updated_compose)
    write(auth_file, updated_settings)
    compose(runtime, 'restart', 'synapse')
    compose(runtime, 'up', '-d', '--no-deps', service)
    print(f'{bridge.capitalize()} bridge enabled. Connect the account from Element.')



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT/'runtime', help='Private local state directory.')
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('prepare', help='Render configuration without starting any container.')
    p.add_argument('--host', default='localhost')
    p.add_argument('--bind', default='127.0.0.1')
    p.add_argument('--port', type=int, default=18081)
    p.add_argument('--owner', default='owner', help='Legacy bridge identifier only; does not create a user.')
    p.add_argument('--project', default='matrix-lan-clean')
    p.add_argument('--core', action='store_true')
    p.add_argument('--telegram-config', type=Path)
    p.add_argument('--cbmrs-origin', default='')
    for action in ('start', 'status', 'stop', 'password', 'ca', 'bridge-catalog'):
        sub.add_parser(action)
    bridge_parser = sub.add_parser('enable-bridge', help='Add WhatsApp or Telegram to an existing installation.')
    bridge_parser.add_argument('bridge', choices=('whatsapp', 'telegram'))
    bridge_parser.add_argument('--telegram-config', type=Path)
    sub.add_parser('enable-telegram').add_argument('telegram_config', type=Path)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    auth_file = runtime / 'bootstrap.private.json'
    if args.action == 'prepare':
        if runtime.exists() or runtime.is_symlink():
            raise ValueError('This runtime directory already exists. Specify a new directory to prepare a fresh installation.')
        settings = choose_setup(args)
        runtime.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='runtime-prepare-',dir=runtime.parent) as temp:
            staging=Path(temp)/'state-tree'
            state_directory(staging)
            build_contexts(staging, replacements(settings))
            configure(staging, settings)
            write(staging/auth_file.name, settings)
            staging.rename(runtime)
        print(f'Prepared {len(settings["services"])} services in {runtime}.')
        return
    settings = load(auth_file)
    if args.action == 'bridge-catalog':
        print(json.dumps(bridge_catalog(runtime, settings), ensure_ascii=False, indent=2))
    elif args.action == 'password':
        print(settings['password'])
    elif args.action == 'status':
        compose(runtime, 'ps')
    elif args.action == 'stop':
        compose(runtime, 'stop')
    elif args.action == 'ca':
        proc = compose(runtime, 'exec', '-T', 'caddy', 'cat', '/data/caddy/pki/authorities/local/root.crt', capture_output=True, text=True)
        write(runtime/'caddy-root.crt', proc.stdout, mode=0o644)
        print(runtime/'caddy-root.crt')
    else:
        if os.name != 'posix' or os.geteuid() != 0:
            raise ValueError('Run on the Linux Docker host with sudo so bind mounts have the correct owner.')
        if args.action == 'enable-telegram':
            enable_bridge(runtime, settings, auth_file, 'telegram', args.telegram_config)
        elif args.action == 'enable-bridge':
            enable_bridge(runtime, settings, auth_file, args.bridge, args.telegram_config)
        else:
            start(runtime, settings, auth_file)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, subprocess.CalledProcessError, FileNotFoundError) as error:
        print(f'Install error: {error}', file=sys.stderr)
        sys.exit(1)
