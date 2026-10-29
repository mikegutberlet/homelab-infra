#!/usr/bin/env python3
"""Merge a pull request, pull main and run the Ansible play it needs, from the terminal.

Goobs runs this on the Mac (needs the GitHub CLI, `gh auth login` once, and the Ansible venv):

    python3 docs/homelab_merge.py 115              # show the plan, ask, merge, pull, deploy
    python3 docs/homelab_merge.py 115 --dry-run    # only show the plan; changes nothing
    python3 docs/homelab_merge.py 115 --no-deploy  # merge and pull, run no play
    python3 docs/homelab_merge.py 115 --yes        # skip the question

What it does, in order:
  1. Reads the PR with `gh pr view` and works out the play from the files it changes:
       stacks/<vm>/...            ->  site.yml --limit <vm> --tags stacks   (one run per VM)
       ansible/roles/ops/...      ->  site.yml --tags ops
     Anything else under ansible/ or tofu/ (other roles, site.yml, inventory, playbooks, OpenTofu) is
     only listed: it is not run, see docs/wiki/operations/deploy-changes.md. So are the n8n sysadmin
     sources (`python3 docs/sysadmin_deploy.py`) and bind-mounted config files that need a container
     restart.
  2. Shows the plan and asks. A draft PR is marked ready, then merged with a merge commit (the
     history's "Merge pull request #n"). A PR already merged in the browser skips the merge.
  3. `git checkout main && git pull --ff-only`, then checks that the PR's merge commit is in your
     checkout. If it is not, it stops before any play: a play run before the merge deploys old code.
  4. Runs the plays from ansible/, one at a time, and stops at the first failure.
Needs a clean tracked tree (untracked files are fine). Secrets are never read or printed here.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys
from typing import NoReturn

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = "mikegutberlet/homelab-infra"
VMS = ["edge", "media", "gpu", "apps", "ai", "transcode"]
VENV_PLAYBOOK = pathlib.Path.home() / ".local/share/homelab-ansible/bin/ansible-playbook"
# Bind-mounted config files: compose does not recreate the container when only these change.
RESTART_FILES = [
    "traefik/dynamic/routes.yml", "tsdproxy/apps.yaml", "homepage/services.yaml",
    "recyclarr.yml", "olivetin/config.yaml", "nextcloud/nginx.conf",
]
SYSADMIN_SOURCES = ("stacks/apps/n8n/sysadmin/", "docs/build_sysadmin_workflow.py")


def plan(paths):
    """The changed paths -> (plays, notes). A play is (label, ansible-playbook arguments)."""
    vms, ops, notes = set(), False, []
    manual, restart, sysadmin = [], [], False
    for p in paths:
        parts = p.split("/")
        if parts[0] == "stacks" and len(parts) > 2 and parts[1] in VMS:
            vms.add(parts[1])
            restart += [p for f in RESTART_FILES if p.endswith(f)]
            if p.startswith(SYSADMIN_SOURCES) or (p.startswith("stacks/apps/n8n/") and p.endswith(".workflow.json")):
                sysadmin = True
        elif p.startswith("ansible/roles/ops/"):
            ops = True
        elif p.startswith(SYSADMIN_SOURCES):
            sysadmin = True
        elif p.startswith(("ansible/", "tofu/")):
            manual.append(p)
    plays = [("stacks on %s" % vm, ["--limit", vm, "--tags", "stacks"]) for vm in VMS if vm in vms]
    if ops:
        plays.append(("ops on every VM", ["--tags", "ops"]))
    if restart:
        notes.append("Bind-mounted config changed, compose will not recreate the container: restart it "
                     "(deploy-changes.md, Gotchas): " + ", ".join(restart))
    if sysadmin:
        notes.append("n8n sysadmin sources changed: also run `python3 docs/sysadmin_deploy.py`")
    if manual:
        notes.append("Not run by this script, deploy by hand (deploy-changes.md): " + ", ".join(manual))
    return plays, notes


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, **kw)


def gh_view(pr):
    out = run(["gh", "pr", "view", str(pr), "--repo", REPO, "--json",
               "number,title,state,isDraft,headRefName,files,mergeCommit"],
              capture_output=True).stdout
    return json.loads(out)


def die(msg) -> NoReturn:
    print("homelab_merge: " + msg, file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Merge a PR, pull main, run the play it needs.")
    ap.add_argument("pr", type=int)
    ap.add_argument("--dry-run", action="store_true", help="show the plan only")
    ap.add_argument("--no-deploy", action="store_true", help="merge and pull, run no play")
    ap.add_argument("--yes", action="store_true", help="do not ask")
    a = ap.parse_args()
    if not shutil.which("gh"):
        die("the GitHub CLI is missing: brew install gh && gh auth login")
    try:
        info = gh_view(a.pr)
    except subprocess.CalledProcessError as e:
        die("gh could not read PR #%d (%s)" % (a.pr, (e.stderr or "").strip() or "no output"))
    state = info["state"]
    if state == "CLOSED":
        die("PR #%d is closed without a merge" % a.pr)
    paths = [f["path"] for f in info["files"]]
    plays, notes = plan(paths)
    if len(paths) >= 100:
        notes.append("gh lists at most 100 files; the plan may be incomplete, check the PR")

    print("PR #%d %s (%s, %s)" % (info["number"], info["title"], info["headRefName"],
                                  "draft" if info["isDraft"] else state.lower()))
    print("Changes %d files." % len(paths))
    if state == "OPEN":
        print("Merge:  %s gh pr merge %d --merge" % ("gh pr ready; " if info["isDraft"] else "", a.pr))
    else:
        print("Merge:  already merged")
    print("Pull:   git checkout main && git pull --ff-only, then check the merge commit is there")
    if a.no_deploy:
        print("Deploy: none (--no-deploy)")
    elif plays:
        for label, args in plays:
            print("Deploy: ansible-playbook playbooks/site.yml " + " ".join(args) + "   # " + label)
    else:
        print("Deploy: no play needed for these files")
    for n in notes:
        print("Note:   " + n)
    if a.dry_run:
        return

    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                      capture_output=True, text=True).stdout.strip():
        die("tracked files are modified in %s; commit or stash them first" % ROOT)
    if not a.yes:
        question = "Merge and deploy? [y/N] " if state == "OPEN" else "Pull and deploy? [y/N] "
        if input(question).strip().lower() != "y":
            die("stopped, nothing changed")

    if state == "OPEN":
        if info["isDraft"]:
            run(["gh", "pr", "ready", str(a.pr), "--repo", REPO])
        run(["gh", "pr", "merge", str(a.pr), "--repo", REPO, "--merge"])
        info = gh_view(a.pr)
    run(["git", "checkout", "main"], cwd=ROOT)
    run(["git", "pull", "--ff-only"], cwd=ROOT)
    oid = (info.get("mergeCommit") or {}).get("oid")
    if not oid:
        die("PR #%d shows no merge commit yet; check it on GitHub, then re-run" % a.pr)
    if subprocess.run(["git", "merge-base", "--is-ancestor", oid, "HEAD"], cwd=ROOT).returncode != 0:
        die("merge commit %s is not in your checkout; no play was run (it would deploy old code)" % oid[:7])
    print("Merged and pulled: %s" % oid[:7])

    if a.no_deploy:
        return
    if plays:
        playbook = str(VENV_PLAYBOOK) if VENV_PLAYBOOK.exists() else shutil.which("ansible-playbook")
        if not playbook:
            die("ansible-playbook not found (venv %s); the merge is done, run the plays by hand" % VENV_PLAYBOOK)
        for label, args in plays:
            print("\n== " + label)
            if subprocess.run([playbook, "playbooks/site.yml"] + args, cwd=ROOT / "ansible").returncode != 0:
                die("the play for '%s' failed; later plays were not run" % label)
    for n in notes:
        print("Still to do: " + n)


if __name__ == "__main__":
    main()
