"""Prove, with IAM's own simulator, that the DCP execution role cannot run a shell document.

Run by an OPERATOR who holds ``iam:SimulatePrincipalPolicy`` -- never by DCP, whose role must
not hold it (SECURITY.md §8, ``OPEN-87``). DCP does not "prove" the deny at runtime by trying a
shell document: if the deny were missing, that attempt would run a shell.

    python scripts/verify_aws_role.py --role-arn arn:aws:iam::123456789012:role/DcpExecution \
        --region eu-west-1 [--profile nonprod]

Exit 0 only when both shell documents are denied and the registered document is allowed.
This is a manual check: record the date and result in PROGRESS.md.
"""

from __future__ import annotations

import argparse
import re
import sys

ROLE_ARN = re.compile(r"arn:aws:iam::(\d{12}):role/[A-Za-z0-9+=,.@_/-]{1,128}")
DOCUMENT = "DCP-ServiceControl"
SHELL_DOCUMENTS = ("AWS-RunShellScript", "AWS-RunPowerShellScript")


def evaluate(decisions: dict[str, str]) -> list[str]:
    """``{document: decision}`` to a list of problems (empty means the role is as required)."""
    problems = []
    for name in SHELL_DOCUMENTS:
        if decisions.get(name) != "explicitDeny":
            problems.append(f"{name} is not explicitly denied (decision: {decisions.get(name)})")
    if decisions.get(DOCUMENT) != "allowed":
        problems.append(f"{DOCUMENT} is not allowed (decision: {decisions.get(DOCUMENT)})")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    args = parser.parse_args(argv)
    match = ROLE_ARN.fullmatch(args.role_arn)
    if match is None:
        print("role-arn is not an IAM role ARN", file=sys.stderr)
        return 2
    import boto3  # imported late: the unit tests exercise evaluate() without the SDK

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    iam = session.client("iam")
    account = match.group(1)
    arns = {
        name: f"arn:aws:ssm:{args.region}:{account}:document/{name}" for name in (DOCUMENT,)
    } | {name: f"arn:aws:ssm:{args.region}::document/{name}" for name in SHELL_DOCUMENTS}
    decisions: dict[str, str] = {}
    for name, arn in arns.items():
        response = iam.simulate_principal_policy(
            PolicySourceArn=args.role_arn, ActionNames=["ssm:SendCommand"], ResourceArns=[arn]
        )
        decisions[name] = response["EvaluationResults"][0]["EvalDecision"]
    problems = evaluate(decisions)
    for name, decision in decisions.items():
        print(f"{name}: {decision}")
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    print("OK" if not problems else "NOT OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
