"""Проверяет готовность по mTLS gRPC или admin /readyz без вывода служебных ключей."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def grpc_ready(root, service, port):
    name = f'{service}.internal.cellestial.ru'
    tls = root / 'tls' / 'probe'
    with tempfile.TemporaryDirectory() as directory:
        headers = Path(directory) / 'headers'
        result = subprocess.run([
            'curl', '--silent', '--fail', '--max-time', '5', '--http2',
            '--cacert', str(tls / 'ca.crt'), '--cert', str(tls / 'probe.crt'), '--key', str(tls / 'probe.key'),
            '--resolve', f'{name}:{port}:127.0.0.1', '--dump-header', str(headers),
            '-H', 'content-type: application/grpc', '-H', 'te: trailers',
            '--data-binary', '@-', f'https://{name}:{port}/grpc.health.v1.Health/Check',
        ], input=b'\0' * 5, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        return (result.returncode == 0 and result.stdout == b'\0\0\0\0\2\x08\x01'
                and b'grpc-status: 0' in [line.lower() for line in headers.read_bytes().splitlines()])


def admin_ready(port):
    """Готовность сервиса без gRPC (OnlineConf): /readyz admin-порта на 127.0.0.1."""
    result = subprocess.run(['curl', '--silent', '--fail', '--max-time', '5', '--output', '/dev/null',
                             f'http://127.0.0.1:{port}/readyz'], stderr=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def main():
    root, service, check = sys.argv[1:]
    base = Path(root)
    ports = json.loads((base / 'ports.json').read_text())[service]
    if check != 'ready':
        return 1
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if grpc_ready(base, service, ports['grpc']) if 'grpc' in ports else admin_ready(ports['admin']):
            return 0
        time.sleep(2)
    return 1


if __name__ == '__main__':
    sys.exit(main())
