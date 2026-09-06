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
            self.assertTrue(storage.url(saved_name), 'storage.url() returned an empty value.')
        finally:
            storage.delete(saved_name)
        self.assertFalse(
            storage.exists(saved_name), 'File still exists in the bucket after delete().')
