"""Download corresponding-source archives; never execute or extract vendor code."""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def recipe_sources(text):
    result = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != 'Package':
            continue
        values = {}
        for keyword in node.keywords:
            if keyword.arg in {'name', 'source_url', 'sha256'}:
                values[keyword.arg] = ast.literal_eval(keyword.value)
        if {'name', 'source_url', 'sha256'} <= values.keys():
            url = values['source_url'].replace('http://', 'https://', 1)
            filename = values['name'] + '-' + url.split('/')[-1]
            if not re.fullmatch(r'[A-Za-z0-9._+-]+', filename):
                raise ValueError('Invalid source archive filename')
            result.append(dict(filename=filename, url=url, sha256=values['sha256']))
    return result


def acquire(entry, output):
    target = output / entry['filename']
    if target.parent != output or not entry['url'].startswith('https://'):
        raise ValueError('Invalid source descriptor')
    expected = entry.get('sha256')
    if target.exists():
        measured = hashlib.sha256(target.read_bytes()).hexdigest()
        if expected and expected != measured:
            raise ValueError('Existing source archive hash mismatch')
    else:
        temporary = target.with_name(target.name + '.partial')
        digest = hashlib.sha256()
        with urllib.request.urlopen(entry['url'], timeout=45) as response, temporary.open('wb') as stream:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                stream.write(chunk)
        measured = digest.hexdigest()
        if expected and expected != measured:
            raise ValueError('Downloaded source archive hash mismatch')
        temporary.replace(target)
    return {**entry, 'sha256': measured, 'bytes': target.stat().st_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    entries = json.loads(args.plan.read_text(encoding='utf-8'))
    names = [entry['filename'] for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate source archive names')
    results, failures = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(acquire, entry, args.output): entry for entry in entries}
        for future in as_completed(pending):
            entry = pending[future]
            try:
                result = future.result()
                results.append(result)
                print('Verified ' + result['filename'], flush=True)
            except Exception as error:
                failures.append({'filename': entry['filename'], 'error': type(error).__name__})
                print('Pending ' + entry['filename'] + ': ' + type(error).__name__, flush=True)
    (args.output / 'sources-manifest.json').write_text(json.dumps(
        {'archives': sorted(results, key=lambda r: r['filename']), 'pending': failures}, indent=2) + '\n', encoding='utf-8')
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
