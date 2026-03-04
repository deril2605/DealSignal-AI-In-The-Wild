from dealsignal.pipeline.extract import generate_event_fingerprint


def test_fingerprint_is_stable_and_sensitive():
    fields = {
        "geography": ["US"],
        "counterparties": ["ExampleCorp"],
        "themes": ["expansion"],
    }
    fp1 = generate_event_fingerprint("Stripe", "Geographic Expansion", fields, "Expanding in US")
    fp2 = generate_event_fingerprint("Stripe", "Geographic Expansion", fields, "Expanding in US")
    fp3 = generate_event_fingerprint("Stripe", "Strategic Partnership", fields, "Expanding in US")
    assert fp1 == fp2
    assert fp1 != fp3

