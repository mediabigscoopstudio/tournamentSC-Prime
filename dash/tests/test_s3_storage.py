"""Live S3 connectivity check for the media-storage migration (see the
USE_S3_MEDIA block in tournamentsc/settings.py). This makes a real network
call to the configured bucket, so it never runs as part of a normal
`manage.py test` — only when RUN_S3_CONNECTIVITY_TEST is explicitly set,
independent of USE_S3_MEDIA, so the bucket/credentials can be verified
before anything is actually cut over to S3.

Run it with:
    RUN_S3_CONNECTIVITY_TEST=1 python manage.py test dash.tests.test_s3_storage -v 2
"""
import os
import time
import unittest

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import SimpleTestCase


@unittest.skipUnless(
    os.environ.get('RUN_S3_CONNECTIVITY_TEST'),
    'Set RUN_S3_CONNECTIVITY_TEST=1 to run this test — it makes real calls to the '
    'configured S3 bucket and is not part of the normal test suite.',
)
class S3StorageConnectivityTests(SimpleTestCase):
    """Upload / read / delete round-trip against the real bucket named by
    AWS_STORAGE_BUCKET_NAME, independent of whether USE_S3_MEDIA is turned
    on yet — this is how to verify the bucket and credentials work
    *before* any media is actually served from S3."""

    def test_upload_read_delete_roundtrip(self):
        from storages.backends.s3boto3 import S3Boto3Storage

        storage = S3Boto3Storage(
            bucket_name=settings.AWS_STORAGE_BUCKET_NAME,
            region_name=settings.AWS_S3_REGION_NAME,
            access_key=settings.AWS_ACCESS_KEY_ID,
            secret_key=settings.AWS_SECRET_ACCESS_KEY,
            location='media',
            default_acl=None,
            # Must match settings.py's STORAGES config — without an explicit
            # regional endpoint, presigned URLs for opt-in regions (e.g.
            # ap-south-2) are built against the legacy global endpoint and
            # get rejected by S3 with IllegalLocationConstraintException the
            # moment a browser actually opens one, even though plain SDK
            # calls (save/exists/open/delete below) work fine regardless.
            endpoint_url=f'https://s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com',
        )
        name = f'_s3_connectivity_test/{int(time.time())}.txt'
        content = b'tournament-sc S3 connectivity check'

        saved_name = storage.save(name, ContentFile(content))
        try:
            self.assertTrue(
                storage.exists(saved_name),
                'File was uploaded but storage.exists() says it is not there.')
            with storage.open(saved_name, 'rb') as f:
                self.assertEqual(
                    f.read(), content, 'Read-back content did not match what was uploaded.')
            url = storage.url(saved_name)
            self.assertTrue(url, 'storage.url() returned an empty value.')
            # SDK calls (save/exists/open above) succeed even with the wrong
            # endpoint — boto3 resolves the real region internally. A
            # presigned URL has no such help: a browser hits it directly, so
            # it must be built against the actual regional endpoint or S3
            # rejects it with IllegalLocationConstraintException the moment
            # anyone opens it. Checking the string here catches that even
            # though every call above would otherwise report success.
            # No leading dot: matches both virtual-hosted style
            # (bucket.s3.<region>.amazonaws.com) and path style
            # (s3.<region>.amazonaws.com/bucket/...) — either is a correct
            # regional endpoint; only the bare global s3.amazonaws.com host
            # (no region) is the bug this guards against.
            expected_host = f's3.{settings.AWS_S3_REGION_NAME}.amazonaws.com'
            self.assertIn(
                expected_host, url,
                f"Presigned URL host doesn't include the region ({expected_host}) — this will "
                f"work for save()/open() above but fail with IllegalLocationConstraintException "
                f"the moment a browser opens the URL. Got: {url}")
        finally:
            storage.delete(saved_name)
        self.assertFalse(
            storage.exists(saved_name), 'File still exists in the bucket after delete().')
