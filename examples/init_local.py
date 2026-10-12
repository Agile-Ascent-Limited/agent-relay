"""Create local demo credentials without printing or overwriting any token."""
import argparse
import json
import os
from pathlib import Path
import secrets


def initialize(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    tokens = {name: secrets.token_urlsafe(32) for name in ('claude', 'codex', 'human', 'watcher')}
    for name, content in {
        'tokens-by-agent.json': json.dumps(tokens, indent=2) + '\n',
        'tokens.json': json.dumps({token: agent for agent, token in tokens.items()}, indent=2) + '\n',
        'human-token.txt': tokens['human'] + '\n',
    }.items():
        with open(path / name, 'x', encoding='utf-8') as stream:
            stream.write(content)
        os.chmod(path / name, 0o600)
    return tokens


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default='.local')
    args = parser.parse_args()
    initialize(args.state)
    print('Local credentials created. Token values were not printed. Restrict Windows ACLs before use.')
