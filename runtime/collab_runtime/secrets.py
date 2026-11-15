"""Read one secret from Google Secret Manager as the runtime's service account.

The runtime runs as `agent-runtime`, so Cloud Run supplies its credentials automatically. No key file is used.
"""


def read_secret(project_id: str, name: str) -> str:
    from google.cloud import secretmanager  # imported here so tests need not install it

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": f"projects/{project_id}/secrets/{name}/versions/latest"})
    return response.payload.data.decode("utf-8").strip()
