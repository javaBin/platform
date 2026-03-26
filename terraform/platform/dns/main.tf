################################################################################
# Route53 Hosted Zones — javaBin domains migrated from Domeneshop
#
# Registrar stays at Domeneshop. Only DNS is in Route53.
# After applying, update NS records at Domeneshop to the output nameservers.
#
# Migration order:
#   1. javabin.com   (simplest — 1 real record)
#   2. javabin.no    (2 real records)
#   3. java.no       (24 records, main org domain)
#   4. teknologihuset.no (16 records, has Domeneshop email/hosting)
################################################################################

# ==============================================================================
# javabin.com
# ==============================================================================

resource "aws_route53_zone" "javabin_com" {
  name = "javabin.com"

  tags = {
    Name = "javabin.com"
  }
}

# Root — currently points to Domeneshop default page (194.63.248.52)
resource "aws_route53_record" "javabin_com_a" {
  zone_id = aws_route53_zone.javabin_com.zone_id
  name    = "javabin.com"
  type    = "A"
  ttl     = 3600
  records = ["194.63.248.52"]
}

resource "aws_route53_record" "javabin_com_aaaa" {
  zone_id = aws_route53_zone.javabin_com.zone_id
  name    = "javabin.com"
  type    = "AAAA"
  ttl     = 3600
  records = ["2a01:5b40:0:248::52"]
}

resource "aws_route53_record" "javabin_com_www" {
  zone_id = aws_route53_zone.javabin_com.zone_id
  name    = "www.javabin.com"
  type    = "A"
  ttl     = 3600
  records = ["194.63.248.52"]
}

resource "aws_route53_record" "javabin_com_dmarc" {
  zone_id = aws_route53_zone.javabin_com.zone_id
  name    = "_dmarc.javabin.com"
  type    = "TXT"
  ttl     = 3600
  records = ["v=DMARC1; p=none"]
}

# ==============================================================================
# javabin.no
# ==============================================================================

resource "aws_route53_zone" "javabin_no" {
  name = "javabin.no"

  tags = {
    Name = "javabin.no"
  }
}

# Root — currently points to Domeneshop default page
resource "aws_route53_record" "javabin_no_a" {
  zone_id = aws_route53_zone.javabin_no.zone_id
  name    = "javabin.no"
  type    = "A"
  ttl     = 3600
  records = ["194.63.248.52"]
}

resource "aws_route53_record" "javabin_no_aaaa" {
  zone_id = aws_route53_zone.javabin_no.zone_id
  name    = "javabin.no"
  type    = "AAAA"
  ttl     = 3600
  records = ["2a01:5b40:0:248::52"]
}

resource "aws_route53_record" "javabin_no_www" {
  zone_id = aws_route53_zone.javabin_no.zone_id
  name    = "www.javabin.no"
  type    = "A"
  ttl     = 3600
  records = ["194.63.248.52"]
}

resource "aws_route53_record" "javabin_no_mx" {
  zone_id = aws_route53_zone.javabin_no.zone_id
  name    = "javabin.no"
  type    = "MX"
  ttl     = 300
  records = ["5 mail.java.no."]
}

resource "aws_route53_record" "javabin_no_dmarc" {
  zone_id = aws_route53_zone.javabin_no.zone_id
  name    = "_dmarc.javabin.no"
  type    = "TXT"
  ttl     = 3600
  records = ["v=DMARC1; p=none"]
}

# ==============================================================================
# java.no — main org domain (Google Workspace)
# ==============================================================================

resource "aws_route53_zone" "java_no" {
  name = "java.no"

  tags = {
    Name = "java.no"
  }
}

# Root A record
resource "aws_route53_record" "java_no_a" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "java.no"
  type    = "A"
  ttl     = 3600
  records = ["16.16.253.0"]
}

# Wildcard — catches all undefined subdomains
resource "aws_route53_record" "java_no_wildcard" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "*.java.no"
  type    = "A"
  ttl     = 3600
  records = ["16.16.253.0"]
}

# ---------- Email (Google Workspace) ----------

resource "aws_route53_record" "java_no_mx" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "java.no"
  type    = "MX"
  ttl     = 3600
  records = [
    "1 ASPMX.L.GOOGLE.COM.",
    "5 ALT1.ASPMX.L.GOOGLE.COM.",
    "5 ALT2.ASPMX.L.GOOGLE.COM.",
    "10 ALT3.ASPMX.L.GOOGLE.COM.",
    "10 ALT4.ASPMX.L.GOOGLE.COM.",
  ]
}

resource "aws_route53_record" "java_no_spf" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "java.no"
  type    = "TXT"
  ttl     = 3600
  records = [
    "v=spf1 mx ip4:212.71.237.26 include:_spf.google.com ~all",
    "google-site-verification=AF0DKgeCRjhwASd4SPtC-wKS8bBFkpQJDReud486Vxw",
    "google-site-verification=VrR4VP57L2aSHRB-jsbpMixnRSI98siDpimdHgpSKo8",
  ]
}

resource "aws_route53_record" "java_no_dmarc" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "_dmarc.java.no"
  type    = "TXT"
  ttl     = 3600
  records = ["v=DMARC1; p=none;"]
}

# ---------- DKIM (Mailchimp) ----------

resource "aws_route53_record" "java_no_dkim_k2" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "k2._domainkey.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["dkim2.mcsv.net."]
}

resource "aws_route53_record" "java_no_dkim_k3" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "k3._domainkey.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["dkim3.mcsv.net."]
}

# ---------- DKIM (SendGrid) ----------

resource "aws_route53_record" "java_no_dkim_s1" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "s1._domainkey.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["s1.domainkey.u8086282.wl033.sendgrid.net."]
}

resource "aws_route53_record" "java_no_dkim_s2" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "s2._domainkey.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["s2.domainkey.u8086282.wl033.sendgrid.net."]
}

# ---------- SendGrid domain authentication ----------

resource "aws_route53_record" "java_no_sendgrid" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "em8650.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["u8086282.wl033.sendgrid.net."]
}

# ---------- Google Sites / custom URLs ----------

resource "aws_route53_record" "java_no_lister" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "lister.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["ghs.googlehosted.com."]
}

# ---------- GitHub Pages subdomains ----------

resource "aws_route53_record" "java_no_github_pages" {
  for_each = toset([
    "javazone-2017",
    "javazone-2018",
    "javazone-2019",
    "javazone-2020",
    "j-team",
    "text-adventure",
  ])

  zone_id = aws_route53_zone.java_no.zone_id
  name    = "${each.key}.java.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["javaBin.github.io."]
}

# ---------- Google site verification (www4 subdomain) ----------

resource "aws_route53_record" "java_no_www4_verification" {
  zone_id = aws_route53_zone.java_no.zone_id
  name    = "www4.java.no"
  type    = "TXT"
  ttl     = 300
  records = ["google-site-verification=GAxI89cdBzVJRVwvA-xLVj7UI5tqDMAHlZ7eomK1OIU"]
}

# ==============================================================================
# teknologihuset.no
#
# Note: Domeneshop email service is active ("email": true) and overrides MX
# records at the DNS level. The Google MX records here are what's configured
# in Domeneshop's API. When email service is disabled and NS is switched to
# Route53, these Google MX records will take effect.
# The webhotel (webxlarge) stays at Domeneshop — the A record points to
# their server and traffic routes there regardless of who serves DNS.
# ==============================================================================

resource "aws_route53_zone" "teknologihuset_no" {
  name = "teknologihuset.no"

  tags = {
    Name = "teknologihuset.no"
  }
}

resource "aws_route53_record" "teknologihuset_no_a" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "teknologihuset.no"
  type    = "A"
  ttl     = 3600
  records = ["13.53.169.151"]
}

resource "aws_route53_record" "teknologihuset_no_www" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "www.teknologihuset.no"
  type    = "A"
  ttl     = 300
  records = ["13.53.169.151"]
}

# MX — Google Workspace (currently masked by Domeneshop email service)
resource "aws_route53_record" "teknologihuset_no_mx" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "teknologihuset.no"
  type    = "MX"
  ttl     = 3600
  records = [
    "10 ASPMX.L.GOOGLE.COM.",
    "20 ALT1.ASPMX.L.GOOGLE.COM.",
    "20 ALT2.ASPMX.L.GOOGLE.COM.",
    "30 ASPMX2.GOOGLEMAIL.COM.",
    "30 ASPMX3.GOOGLEMAIL.COM.",
  ]
}

resource "aws_route53_record" "teknologihuset_no_spf" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "teknologihuset.no"
  type    = "TXT"
  ttl     = 3600
  records = ["v=spf1 include:_spf.domeneshop.no ~all"]
}

resource "aws_route53_record" "teknologihuset_no_dmarc" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_dmarc.teknologihuset.no"
  type    = "TXT"
  ttl     = 3600
  records = ["v=DMARC1; p=quarantine; rua=mailto:dmarc@domeneshop.no"]
}

# GitHub org verification
resource "aws_route53_record" "teknologihuset_no_github_challenge" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_github-challenge-teknologihuset.teknologihuset.no"
  type    = "TXT"
  ttl     = 3600
  records = ["4cadd5f17c"]
}

# Domeneshop email autodiscovery (autoconfig)
resource "aws_route53_record" "teknologihuset_no_autoconfig" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "autoconfig.teknologihuset.no"
  type    = "CNAME"
  ttl     = 3600
  records = ["autoconfig.domeneshop.no."]
}

# Domeneshop email autodiscovery (Outlook/Exchange)
resource "aws_route53_record" "teknologihuset_no_autodiscover" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_autodiscover._tcp.teknologihuset.no"
  type    = "SRV"
  ttl     = 3600
  records = ["0 0 443 autoconfig.domeneshop.no."]
}

# CalDAV service discovery
resource "aws_route53_record" "teknologihuset_no_caldav_srv" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_caldavs._tcp.teknologihuset.no"
  type    = "SRV"
  ttl     = 3600
  records = ["0 0 443 caldav.domeneshop.no."]
}

resource "aws_route53_record" "teknologihuset_no_caldav_txt" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_caldavs._tcp.teknologihuset.no"
  type    = "TXT"
  ttl     = 3600
  records = ["path=/"]
}

# CardDAV service discovery
resource "aws_route53_record" "teknologihuset_no_carddav_srv" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_carddavs._tcp.teknologihuset.no"
  type    = "SRV"
  ttl     = 3600
  records = ["0 0 443 carddav.domeneshop.no."]
}

resource "aws_route53_record" "teknologihuset_no_carddav_txt" {
  zone_id = aws_route53_zone.teknologihuset_no.zone_id
  name    = "_carddavs._tcp.teknologihuset.no"
  type    = "TXT"
  ttl     = 3600
  records = ["path=/"]
}
