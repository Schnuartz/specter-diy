ADDRESS_URI_NETWORKS = {
    "bitcoin": None,
    "liquidnetwork": "liquidv1",
    "liquidtestnet": "liquidtestnet",
    # Keep accepting the older, network-agnostic prefix.
    "liquid": None,
}

NETWORK_URI_SCHEMES = {
    "liquidv1": "liquidnetwork",
    "liquidtestnet": "liquidtestnet",
}

NETWORK_NAMES = {
    "liquidv1": "Liquid Mainnet",
    "liquidtestnet": "Liquid Testnet",
}


def address_uri_scheme(payload):
    """Return a supported address URI scheme from text or a byte stream prefix."""
    if isinstance(payload, bytes):
        separator = payload.find(b":")
        if separator <= 0:
            return None
        try:
            scheme = payload[:separator].decode("ascii").lower()
        except UnicodeDecodeError:
            return None
    else:
        separator = payload.find(":")
        if separator <= 0:
            return None
        scheme = payload[:separator].lower()

    if scheme in ADDRESS_URI_NETWORKS:
        return scheme
    return None
