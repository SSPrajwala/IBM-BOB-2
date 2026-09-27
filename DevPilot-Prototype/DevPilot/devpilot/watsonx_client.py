"""Thin, dependency-free watsonx.ai client.

Reads credentials from the environment (put them in a .env and `source` it,
or export directly) -- never hardcoded, never asked for in chat. If the
credentials aren't set, `available()` is False and every caller in DevPilot
falls back to its deterministic, rule-based text so the tool always demos,
with or without a live watsonx.ai connection.

Required env vars:
    WATSONX_API_KEY      IBM Cloud IAM API key
    WATSONX_PROJECT_ID   watsonx.ai project id
Optional:
    WATSONX_URL          default: https://us-south.ml.cloud.ibm.com
    WATSONX_MODEL_ID     default: ibm/granite-13b-instruct-v2
"""
import json
import os
import time
import urllib.error
import urllib.request

IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
DEFAULT_URL = "https://us-south.ml.cloud.ibm.com"
DEFAULT_MODEL = "ibm/granite-13b-instruct-v2"

_token_cache = {"token": None, "expires_at": 0}


def available():
    return bool(os.environ.get("WATSONX_API_KEY")) and bool(os.environ.get("WATSONX_PROJECT_ID"))


def _get_iam_token(api_key):
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    data = f"grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey={api_key}".encode()
    req = urllib.request.Request(
        IAM_TOKEN_URL, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read())
    _token_cache["token"] = payload["access_token"]
    _token_cache["expires_at"] = time.time() + payload.get("expires_in", 3600)
    return _token_cache["token"]


def generate(prompt, max_tokens=300, temperature=0.3):
    """Returns generated text, or None (with the caller expected to fall
    back) if watsonx.ai isn't configured or the call fails for any reason --
    a demo should never hard-fail because of a network hiccup or an
    exhausted free-tier quota."""
    if not available():
        return None
    api_key = os.environ["WATSONX_API_KEY"]
    project_id = os.environ["WATSONX_PROJECT_ID"]
    url = os.environ.get("WATSONX_URL", DEFAULT_URL)
    model_id = os.environ.get("WATSONX_MODEL_ID", DEFAULT_MODEL)

    try:
        token = _get_iam_token(api_key)
        body = json.dumps({
            "input": prompt,
            "model_id": model_id,
            "project_id": project_id,
            "parameters": {"max_new_tokens": max_tokens, "temperature": temperature, "decoding_method": "greedy"},
        }).encode()
        req = urllib.request.Request(
            f"{url}/ml/v1/text/generation?version=2024-05-31",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read())
        return payload["results"][0]["generated_text"].strip()
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as e:
        return None
