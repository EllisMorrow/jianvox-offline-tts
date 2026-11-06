"""Explicit user-triggered release check; never downloads or installs updates."""
import json
from urllib.request import Request, urlopen
from packaging.version import Version

RELEASE_URL = 'https://github.com/EllisMorrow/jianvox-offline-tts/releases/latest'
API_URL = 'https://api.github.com/repos/EllisMorrow/jianvox-offline-tts/releases/latest'


def is_newer(remote, local):
    return Version(remote) > Version(local)


def latest_version():
    request = Request(API_URL, headers={'User-Agent': 'JianVox-update-check',
                                      'Accept': 'application/vnd.github+json'})
    with urlopen(request, timeout=10) as response:
        payload = response.read(262145)
    if len(payload) > 262144:
        raise ValueError('Release response too large')
    data = json.loads(payload)
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('Not a stable release')
    version = str(Version(data['tag_name']))
    return version
