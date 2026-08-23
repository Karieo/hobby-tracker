#!/usr/bin/env python3
"""Mint, list and revoke API tokens for the export endpoint.

    python3 scripts/api_token.py --create "list optimiser"
    python3 scripts/api_token.py --list
    python3 scripts/api_token.py --revoke <id>

`api_tokens` was created by migration 001 and had no consumer until
`GET /api/export/inventory`. This is the smallest thing that fills it: no UI,
because a credential minted twice a year does not need one.

**The plaintext is printed once and never stored.** Only its SHA-256 goes in
the database, so a copy of the database is not a copy of the tokens — losing
one means revoking it and minting another, which is the trade that makes that
true. Revoke is here rather than deferred because a credential you cannot
withdraw is a liability, and it is four lines.
"""

import argparse
import hashlib
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402


def owner(conn):
    """The single user this app has. Auth is single-user by design."""
    row = conn.execute(
        "SELECT * FROM users WHERE role = 'owner' ORDER BY created_at "
        'LIMIT 1').fetchone()
    return row or conn.execute('SELECT * FROM users ORDER BY created_at '
                               'LIMIT 1').fetchone()


def create(device_name):
    with db.connect() as conn:
        user = owner(conn)
    if not user:
        print('No user in the database — start the app once so it creates one.',
              file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    db.create_api_token(user['id'],
                        hashlib.sha256(token.encode()).hexdigest(),
                        device_name)
    print(f'Token for {user["name"]}'
          + (f' ({device_name})' if device_name else '') + ':\n')
    print(f'  {token}\n')
    print('Copy it now — it is not stored and cannot be shown again.')
    print('Use it as:  Authorization: Bearer <token>')
    return 0


def show():
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT t.id, t.device_name, t.created_at, t.last_used_at, u.name
              FROM api_tokens t JOIN users u ON u.id = t.user_id
             ORDER BY t.created_at
        """).fetchall()
    if not rows:
        print('No tokens.')
        return 0
    for row in rows:
        used = row['last_used_at'] or 'never used'
        print(f'  {row["id"]}  {row["device_name"] or "unnamed":<24} '
              f'{row["name"]:<10} created {row["created_at"][:10]}  {used}')
    return 0


def revoke(token_id):
    with db.connect() as conn:
        found = conn.execute('SELECT device_name FROM api_tokens WHERE id = ?',
                             (token_id,)).fetchone()
        if not found:
            print(f'No token {token_id}', file=sys.stderr)
            return 1
        conn.execute('DELETE FROM api_tokens WHERE id = ?', (token_id,))
    print(f'Revoked {token_id} ({found["device_name"] or "unnamed"}).')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--create', metavar='NAME', nargs='?', const='',
                    help='mint a token, optionally naming what it is for')
    ap.add_argument('--list', action='store_true', help='show existing tokens')
    ap.add_argument('--revoke', metavar='ID', help='delete a token by id')
    ap.add_argument('--db', help='override the database path')
    args = ap.parse_args(argv)
    if args.db:
        db.DB_PATH = args.db

    if args.revoke:
        return revoke(args.revoke)
    if args.list:
        return show()
    if args.create is not None:
        return create(args.create or None)
    ap.print_help()
    return 1


if __name__ == '__main__':
    sys.exit(main())
