"""SMTP backend that verifies TLS against certifi's CA bundle.

Python builds that ship without system CA certificates (e.g. the python.org macOS
installer) fail with `CERTIFICATE_VERIFY_FAILED`. Using certifi makes email work
the same on every machine, while still fully verifying the server certificate.
"""
import ssl
from functools import cached_property

import certifi
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPBackend


class EmailBackend(DjangoSMTPBackend):
    @cached_property
    def ssl_context(self):
        return ssl.create_default_context(cafile=certifi.where())
