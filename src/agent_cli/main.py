"""Local host entry point; importing this module does not initialize the runtime."""


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="agenthub", description="AgentHub local agent CLI")
    parser.add_argument("--version", action="version", version="agenthub 0.1.0")
    parser.parse_args()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
