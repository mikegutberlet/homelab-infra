#!/usr/bin/env python3
"""Checks for docs/homelab_merge.py. No network, no lab access, no real GitHub (a fake `gh`):

    python3 docs/test_homelab_merge.py

  - plan(): which changed paths give which play, and which are only listed
  - --dry-run changes nothing (no ready, no merge)
  - a merge commit that is in the checkout runs the plays, in order
  - a merge commit that is NOT in the checkout stops before any play
Exit 1 on any failure.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docs/homelab_merge.py"
spec = importlib.util.spec_from_file_location("homelab_merge", SCRIPT)
hm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hm)
fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else "  " + str(detail)))
    if not ok:
        fails.append(name)


def args(paths):
    plays, notes = hm.plan(paths)
    return [a for _, a in plays], notes


# plan()
p, n = args(["stacks/apps/compose.yaml", "stacks/apps/hermes/init.sh", "docs/wiki/services/apps.md"])
check("one stacks play for apps", p == [["--limit", "apps", "--tags", "stacks"]] and not n, (p, n))
p, n = args(["stacks/media/compose.yaml", "stacks/edge/secrets.sops.env", "stacks/gpu/compose.yaml"])
check("stacks plays run one VM each, in lab order",
      p == [["--limit", v, "--tags", "stacks"] for v in ("edge", "media", "gpu")], p)
p, n = args(["ansible/roles/ops/files/homelab-agent"])
check("ops role -> --tags ops", p == [["--tags", "ops"]], p)
p, n = args(["docs/wiki/x.md", "README.md", "stacks/apps/hermes/skills/homelab/SKILL.md"])
check("docs and skills: no play for docs, apps stack for the skill", p == [["--limit", "apps", "--tags", "stacks"]], p)
p, n = args(["docs/wiki/x.md", "CLAUDE.md"])
check("docs only: no play, no note", p == [] and n == [], (p, n))
p, n = args(["ansible/roles/lanfw/tasks/main.yml", "ansible/inventory/hosts.yml", "tofu/terraform.tfvars"])
check("other roles, inventory, tofu: listed, not run", p == [] and len(n) == 1 and "lanfw" in n[0] and "tofu" in n[0], (p, n))
p, n = args(["stacks/edge/traefik/dynamic/routes.yml"])
check("bind-mounted config: stacks play plus restart note", len(p) == 1 and "restart" in n[0], (p, n))
p, n = args(["stacks/apps/n8n/sysadmin/settings.js", "stacks/apps/n8n/homelab-sysadmin.workflow.json"])
check("sysadmin sources: stacks play plus sysadmin_deploy note", len(p) == 1 and "sysadmin_deploy" in n[0], (p, n))

# end to end with a fake gh and a fake ansible-playbook, in a throwaway clone
tmp = pathlib.Path(tempfile.mkdtemp())


def sh(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
origin, clone, other = tmp / "origin.git", tmp / "clone", tmp / "other"
sh(["git", "init", "-q", "--bare", "-b", "main", str(origin)], tmp)
sh(["git", "clone", "-q", str(origin), str(clone)], tmp)
(clone / "docs").mkdir()
(clone / "ansible").mkdir()
(clone / "docs/homelab_merge.py").write_text(SCRIPT.read_text())
(clone / "a.txt").write_text("1")
sh(git + ["add", "-A"], clone)
sh(git + ["commit", "-qm", "init"], clone)
sh(["git", "push", "-q", "origin", "main"], clone)
head = sh(["git", "rev-parse", "HEAD"], clone)
sh(["git", "clone", "-q", str(origin), str(other)], tmp)  # a commit that never reaches the clone
(other / "b.txt").write_text("2")
sh(git + ["add", "-A"], other)
sh(git + ["commit", "-qm", "elsewhere"], other)
stray = sh(["git", "rev-parse", "HEAD"], other)
sh(["git", "fetch", "-q", str(other), "main"], clone)  # object exists locally, but is not on main
bindir = tmp / "bin"
bindir.mkdir()
log = tmp / "calls.log"
(bindir / "gh").write_text('#!/bin/sh\necho "gh $*" >> %s\n'
                           'if [ "$2" = view ]; then cat %s/pr.json; fi\n' % (log, tmp))
(bindir / "ansible-playbook").write_text('#!/bin/sh\necho "ansible-playbook $*" >> %s\n' % log)
for f in bindir.iterdir():
    f.chmod(0o755)
env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]), HOME=str(tmp))


def pr(oid, state="OPEN", draft=True):
    (tmp / "pr.json").write_text(json.dumps({
        "number": 9, "title": "t", "state": state, "isDraft": draft, "headRefName": "hermes/t",
        "files": [{"path": "stacks/apps/compose.yaml"}, {"path": "stacks/media/compose.yaml"}],
        "mergeCommit": {"oid": oid} if oid else None}))


def go(*extra):
    log.write_text("")
    r = subprocess.run([sys.executable, str(clone / "docs/homelab_merge.py"), "9"] + list(extra),
                       cwd=clone, env=env, text=True, capture_output=True)
    return r, log.read_text()


pr(None)
r, calls = go("--dry-run")
check("dry-run prints both plays", r.returncode == 0 and r.stdout.count("ansible-playbook playbooks/site.yml") == 2, r.stdout + r.stderr)
check("dry-run only reads", calls.strip().splitlines() == ["gh pr view 9 --repo %s --json number,title,state,isDraft,headRefName,files,mergeCommit" % hm.REPO], calls)

pr(head, state="MERGED", draft=False)
r, calls = go("--yes")
plays = [l for l in calls.splitlines() if l.startswith("ansible-playbook")]
check("merged commit in checkout: both plays run, in lab order (media before apps)",
      r.returncode == 0 and plays == ["ansible-playbook playbooks/site.yml --limit media --tags stacks",
                                      "ansible-playbook playbooks/site.yml --limit apps --tags stacks"], (r.stdout, r.stderr, calls))
check("already merged: no ready, no merge", " pr merge" not in calls and " pr ready" not in calls, calls)

pr(stray, state="MERGED", draft=False)
r, calls = go("--yes")
check("merge commit missing from checkout: stops, no play",
      r.returncode == 1 and "ansible-playbook" not in calls and "old code" in r.stderr, (r.stdout, r.stderr, calls))

pr(head)
r, calls = go("--yes", "--no-deploy")
check("open draft: ready then merge, no play",
      r.returncode == 0 and "gh pr ready 9" in calls and "gh pr merge 9 --repo %s --merge" % hm.REPO in calls
      and "ansible-playbook" not in calls, (r.stdout, r.stderr, calls))

(clone / "a.txt").write_text("dirty")
r, calls = go("--yes")
check("modified tracked file: refuses before merging", r.returncode == 1 and " pr merge" not in calls, (r.stderr, calls))

sys.exit(1 if fails else 0)
