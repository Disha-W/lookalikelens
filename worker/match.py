"""Decide whether a certificate's domains target the watched brand.

Deliberately simple and explainable:
- lowercase + common look-alike character fold (0->o, 1->l, 3->e, 4->a,
  5->s, 7->t, 8->b, @->a, $->s), hyphens/dots removed
- flag when a watched term appears inside a domain
- never flag the owned domain itself or any of its subdomains
  (login.butterpaperbakes.shop is the brand's own site, not a case)

A flagged domain is a CANDIDATE to review, not a confirmed fake.
CT observes certificate issuance, not domain registrations.
"""


def squash(text):
    """Lowercase, fold look-alike characters, keep only letters/digits."""
    fold = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s"})
    return "".join(c for c in text.lower().translate(fold) if c.isalnum())


def is_owned(domain, owned_domains):
    """True for the owned domain itself or any dot-boundary subdomain of it."""
    clean = domain.lower().lstrip("*.")
    return any(clean == o or clean.endswith("." + o) for o in owned_domains)


def find_match(domains, watch_terms, owned_domains):
    """Return (domain, term) for the first flagged domain, else None."""
    for domain in domains or []:
        clean = domain.lower().lstrip("*.")
        if is_owned(clean, owned_domains):
            continue
        squashed = squash(clean)
        for term in watch_terms:
            needle = squash(term)
            if needle and needle in squashed:
                return (clean, term)
    return None
