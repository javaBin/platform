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

resource "aws_route53_record" "javabin_com_www_aaaa" {
  zone_id = aws_route53_zone.javabin_com.zone_id
  name    = "www.javabin.com"
  type    = "AAAA"
  ttl     = 3600
  records = ["2a01:5b40:0:248::52"]
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

resource "aws_route53_record" "javabin_no_www_aaaa" {
  zone_id = aws_route53_zone.javabin_no.zone_id
  name    = "www.javabin.no"
  type    = "AAAA"
  ttl     = 3600
  records = ["2a01:5b40:0:248::52"]
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

# ==============================================================================
# aws.javabin.no, aws.java.no, aws.javazone.no → IAM Identity Center SSO portal
#
# CloudFront + ACM for HTTPS. CloudFront Function handles the 302 redirect.
# No S3 bucket needed — dummy origin since the function intercepts all requests.
# ==============================================================================

locals {
  # SSO redirect hostname => zone it lives in. Keep aws.javabin.no as the
  # cert's domain_name below; the others are SANs.
  sso_redirect_hosts = {
    "aws.javabin.no"  = aws_route53_zone.javabin_no.zone_id
    "aws.java.no"     = aws_route53_zone.java_no.zone_id
    "aws.javazone.no" = var.javazone_zone_id
  }
}

# ACM certificate in us-east-1 (required for CloudFront)
resource "aws_acm_certificate" "sso_redirect" {
  provider                  = aws.us_east_1
  domain_name               = "aws.javabin.no"
  subject_alternative_names = ["aws.java.no", "aws.javazone.no"]
  validation_method         = "DNS"

  tags = {
    Name = "aws.javabin.no"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "sso_redirect_cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.sso_redirect.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = local.sso_redirect_hosts[each.key]
  name            = each.value.name
  type            = each.value.type
  ttl             = 300
  records         = [each.value.record]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "sso_redirect" {
  provider                = aws.us_east_1
  certificate_arn         = aws_acm_certificate.sso_redirect.arn
  validation_record_fqdns = [for record in aws_route53_record.sso_redirect_cert_validation : record.fqdn]
}

# CloudFront Function — redirects all requests to the SSO portal with /start path
resource "aws_cloudfront_function" "sso_redirect" {
  name    = "${var.project}-sso-redirect"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOF
    function handler(event) {
      return {
        statusCode: 302,
        statusDescription: 'Found',
        headers: {
          location: { value: '${var.sso_portal_url}' },
          'cache-control': { value: 'no-cache' }
        }
      };
    }
  EOF
}

# CloudFront distribution — uses function for redirect (not S3)
resource "aws_cloudfront_distribution" "sso_redirect" {
  enabled         = true
  aliases         = keys(local.sso_redirect_hosts)
  comment         = "aws.javabin.no, aws.java.no, aws.javazone.no → SSO portal redirect"
  is_ipv6_enabled = true
  price_class     = "PriceClass_100"

  # Dummy origin — CloudFront requires one but the function intercepts all requests
  origin {
    domain_name = "invalid.invalid"
    origin_id   = "dummy"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "dummy"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 86400
    max_ttl     = 86400

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.sso_redirect.arn
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.sso_redirect.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  tags = {
    Name = "aws.javabin.no-redirect"
  }

  depends_on = [aws_acm_certificate_validation.sso_redirect]
}

# Route53 alias → CloudFront (instead of S3 directly)
resource "aws_route53_record" "sso_redirect" {
  for_each = local.sso_redirect_hosts
  zone_id  = each.value
  name     = each.key
  type     = "A"

  alias {
    name                   = aws_cloudfront_distribution.sso_redirect.domain_name
    zone_id                = aws_cloudfront_distribution.sso_redirect.hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "sso_redirect_aaaa" {
  for_each = local.sso_redirect_hosts
  zone_id  = each.value
  name     = each.key
  type     = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.sso_redirect.domain_name
    zone_id                = aws_cloudfront_distribution.sso_redirect.hosted_zone_id
    evaluate_target_health = false
  }
}

moved {
  from = aws_route53_record.sso_redirect
  to   = aws_route53_record.sso_redirect["aws.javabin.no"]
}

moved {
  from = aws_route53_record.sso_redirect_aaaa
  to   = aws_route53_record.sso_redirect_aaaa["aws.javabin.no"]
}
