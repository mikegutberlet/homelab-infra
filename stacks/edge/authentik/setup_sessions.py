# Keep a login for 30 days instead of asking for password + MFA code every browser session.
# Idempotent. Run from the Mac:
#   ssh goobs@192.168.1.50 'sudo docker compose -f /opt/stacks/edge/compose.yaml exec -T authentik-server ak shell' < stacks/edge/authentik/setup_sessions.py
# Admin UIs are tailnet-only, so a 30-day session is an acceptable trade (2026-11-05).
from authentik.stages.authenticator_validate.models import AuthenticatorValidateStage
from authentik.stages.user_login.models import UserLoginStage

login = UserLoginStage.objects.get(name="default-authentication-login")
login.session_duration = "days=30"   # was seconds=0 = until the browser closes
login.remember_me_offset = "seconds=0"
login.save()

mfa = AuthenticatorValidateStage.objects.get(name="default-authentication-mfa-validation")
mfa.last_auth_threshold = "days=30"  # was seconds=0 = a code on every login
mfa.save()

print("login", login.session_duration, "| mfa threshold", mfa.last_auth_threshold)
