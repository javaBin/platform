# Task 2b — Ingress Module (ALB, ACM, Route53)

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 2a (networking — needs VPC, subnets, ALB SG)
**Blocks:** 2d (compute needs HTTPS listener), 3a (service-routing module needs ALB)

## Objective

Create a fresh ALB, new wildcard ACM certificate, and Route53 records for `*.javazone.no`.
Do NOT touch the existing Default-ALB or its CDK-managed cert.

## Context

Read `phases/phase-2-platform.md` Task 2 for full spec.
Reference: `terraform/ingress/` (copied from ai-platform-infra).

**Confirmed decisions:**
- Region: eu-central-1
- Domain: javazone.no (Route53 zone exists, use data source for zone ID only)
- Fresh ALB (`javabin-platform-alb`) — existing Default-ALB is CDK-managed, untouched
- Fresh ACM cert (`*.javazone.no`) — existing cert is CDK-managed, untouched
- We add DNS records to the existing zone but do NOT manage the zone itself

## Deliverables

### `terraform/platform/ingress/`
- `main.tf` — Route53 zone (data source/import), ACM cert, ALB, listeners
- `variables.tf` — project, domain, public_subnet_ids, alb_sg_id
- `outputs.tf` — alb_arn, alb_dns_name, alb_zone_id, https_listener_arn

## Spec

| Resource | Detail |
|----------|--------|
| Route53 | Data source for existing `javazone.no` zone — add records only, do NOT manage the zone |
| ACM cert | `*.javazone.no` (new cert, DNS validation via Route53 data source) |
| ALB | `javabin-platform-alb`, internet-facing, public subnets |
| HTTPS listener | Port 443, TLS 1.2+, default fixed 404 response |
| HTTP listener | Port 80, redirect to HTTPS (301) |

## Rules
- Route53 zone for javazone.no exists — use `data` source, do NOT import or manage the zone
- We only create new DNS records in it (A records for our ALB)
- Existing records (CDK-managed, EB-managed) are untouched
- ACM cert: create NEW `*.javazone.no` cert (existing one is CDK-managed)
- Fresh ALB: `javabin-platform-alb` — completely separate from existing Default-ALB
- Default listener action is 404 — services register via listener rules later
- Use `var.domain` — never hardcode `javazone.no`
