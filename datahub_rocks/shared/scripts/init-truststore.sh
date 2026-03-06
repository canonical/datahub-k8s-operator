#!/bin/sh

# TODO (mertalpt): Consider changing the default storepass.

# Check if a certificate with the same alias already exists
CERT_EXISTS=$(/usr/lib/jvm/java-17-openjdk/bin/keytool -list -cacerts -alias "$CERT_ALIAS" -storepass "changeit" 2>/dev/null)

if case "$CERT_EXISTS" in "keytool error"*) false;; *) true;; esac; then
  # TODO (mertalpt): Room for improvement.
  # This is used to import Opensearch CA certificates, which are from Let's Encrypt.
  # It is very unlikely that those will change in content but not impossible.
  echo "Certificate with alias '$CERT_ALIAS' already exists. Skipping initialization."
  exit 0
fi

# Add the root CA certificate to the cacerts
/usr/lib/jvm/java-17-openjdk/bin/keytool -importcert -cacerts -file "$CERT_PATH" -alias "$CERT_ALIAS" -storepass "changeit" -noprompt

echo "Certificate '$CERT_ALIAS' imported successfully."
