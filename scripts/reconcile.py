"""Read the mock platform receipt; never replay a send with unknown acceptance."""

import argparse
from app import schema as s
from app.channels import reconcile
from app.db import lock, row, transaction


def main():
    p = argparse.ArgumentParser()
    p.add_argument("tenant")
    p.add_argument("send_id")
    args = p.parse_args()
    with transaction(args.tenant) as c:
        lock(c, "mutation", args.tenant)
        v = row(c, s.sends, args.tenant, args.send_id)
        print(v.id, reconcile(c, args.tenant, v))


if __name__ == "__main__":
    main()
