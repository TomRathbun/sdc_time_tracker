"""Generate a self-signed SSL certificate for local HTTPS."""
import datetime
import ipaddress
import os

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Generate key
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

# Build certificate
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SDC Time Tracker"),
    x509.NameAttribute(NameOID.COMMON_NAME, "sdc-time-tracker.local"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
    .add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.DNSName("sdc-time-tracker.local"),
            x509.IPAddress(ipaddress.IPv4Address("0.0.0.0")),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
            x509.IPAddress(ipaddress.IPv4Address("192.168.8.61")),
        ]),
        critical=False,
    )
    .sign(key, hashes.SHA256())
)

# Write files
os.makedirs("certs", exist_ok=True)

with open("certs/key.pem", "wb") as f:
    f.write(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

with open("certs/cert.pem", "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

print("✓ Generated certs/key.pem and certs/cert.pem (valid for 1 year)")
