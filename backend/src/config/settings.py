import os
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./local.db')
REDIS_URL = os.getenv('REDIS_URL', 'redis://redis:6379/0')
JWT_SECRET = os.environ.get('JWT_SECRET', 'local-development-change-before-deploy')
# Separate key material for sealed trial mappings, so rotating login tokens does not
# strand sealed batches. Defaults to the JWT secret for the local demo only.
BLIND_SEAL_SECRET = os.getenv('BLIND_SEAL_SECRET', JWT_SECRET)
ARTIFACT_DIR = os.getenv('ARTIFACT_DIR', '/artifacts')
