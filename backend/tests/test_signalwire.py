from app.notify.sms.signalwire import compute_signature, verify_signature


def test_verify_signature_accepts_matching_signature():
    url = "https://example.com/webhook"
    params = {"From": "+15555550100", "Body": "hello"}
    token = "secret"

    signature = compute_signature(url, params, token)

    assert verify_signature(url, params, signature, token) is True


def test_verify_signature_rejects_tampered_params():
    url = "https://example.com/webhook"
    params = {"From": "+15555550100", "Body": "hello"}
    token = "secret"

    signature = compute_signature(url, params, token)

    assert verify_signature(url, {"From": "+15555550100", "Body": "tampered"}, signature, token) is False


def test_verify_signature_rejects_wrong_token():
    url = "https://example.com/webhook"
    params = {"From": "+15555550100", "Body": "hello"}

    signature = compute_signature(url, params, "secret")

    assert verify_signature(url, params, signature, "wrong-token") is False


def test_signature_is_order_independent_in_params_dict():
    url = "https://example.com/webhook"
    token = "secret"
    sig1 = compute_signature(url, {"A": "1", "B": "2"}, token)
    sig2 = compute_signature(url, {"B": "2", "A": "1"}, token)
    assert sig1 == sig2
