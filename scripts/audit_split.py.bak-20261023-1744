"""Compare declared development/heldout dimensions; never self-certifies custody."""
import argparse
import json
from pathlib import Path


def audit(manifest):
    collisions = {}
    for dimension in ('wording_family', 'speaker', 'interruption_schedule'):
        development = {r[dimension] for r in manifest['development']}
        heldout = {r[dimension] for r in manifest['heldout']}
        collisions[dimension] = sorted(development & heldout)
    return {'dimension_collisions': collisions, 'disjoint': not any(collisions.values()),
            'custody_independence': 'not established by this structural check'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args()
    result = audit(json.loads(Path(args.manifest).read_text(encoding='utf-8')))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['disjoint'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
