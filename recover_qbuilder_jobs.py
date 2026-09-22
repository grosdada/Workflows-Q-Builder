"""Inspecte ou reprend les jobs Q-Builder apres un crash/reboot ComfyUI."""

import argparse
from pathlib import Path

from network_cluster import parse_servers
from qbuilder_job_ledger import DEFAULT_LEDGER, recover


def main():
    parser = argparse.ArgumentParser(description="Inspecte/reprend les jobs Q-Builder interrompus.")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER)
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--servers", default="")
    parser.add_argument("--retry", action="store_true", help="Relance une fois les jobs perdus ou en erreur sur un autre worker.")
    args = parser.parse_args()
    path = Path(args.ledger)
    if not path.exists():
        raise SystemExit(f"Aucun registre de jobs: {path}. Les jobs lances avant cette mise a jour ne sont pas recuperables automatiquement.")
    report = recover(path, parse_servers(args.server, args.servers), retry=args.retry)
    if not report:
        print("Aucun job a surveiller.")
        return
    for item in report:
        print(f"{item['name']}: {item['state']} — {item['detail']}{item['action']}")


if __name__ == "__main__":
    main()
