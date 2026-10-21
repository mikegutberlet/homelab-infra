# Domain-level forward auth for every app behind Traefik's "authentik" middleware.
# Idempotent. Run on the edge VM:
#   sudo docker exec -i edge-authentik-server-1 ak shell < setup_forward_auth.py
# tail9af84.ts.net is a registrable domain (only ts.net is on the Public Suffix List),
# so one login cookie covers every <app>.tail9af84.ts.net.
from authentik.core.models import Application
from authentik.flows.models import Flow
from authentik.outposts.models import Outpost
from authentik.providers.proxy.models import ProxyMode, ProxyProvider

TAILNET = "tail9af84.ts.net"
AUTH_URL = f"https://auth.{TAILNET}"

provider, _ = ProxyProvider.objects.update_or_create(
    name="homelab-forward-auth",
    defaults={
        "mode": ProxyMode.FORWARD_DOMAIN,
        "external_host": AUTH_URL,
        "cookie_domain": TAILNET,
        "authorization_flow": Flow.objects.get(slug="default-provider-authorization-implicit-consent"),
        "invalidation_flow": Flow.objects.get(slug="default-provider-invalidation-flow"),
        "access_token_validity": "hours=24",
    },
)

Application.objects.update_or_create(
    slug="homelab-apps",
    defaults={"name": "Homelab apps", "provider": provider, "meta_launch_url": "blank://blank"},
)

outpost = Outpost.objects.get(managed="goauthentik.io/outposts/embedded")
outpost.providers.add(provider)
config = outpost.config  # OutpostConfig dataclass; other settings are kept
config.authentik_host = AUTH_URL  # login redirects go here, not to the container address
outpost.config = config
outpost.save()

print("provider", provider.pk, provider.mode, provider.cookie_domain)
print("outpost providers", [p.name for p in outpost.providers.all()], "authentik_host", outpost.config.authentik_host)
