#!/bin/sh

# TODO (mertalpt): Consider changing the default storepass.

# Check if a certificate with the same alias already exists
CERT_EXISTS=$(/usr/lib/jvm/java-17-openjdk/bin/keytool -list -cacerts -alias $CERT_ALIAS -storepass "changeit" 2>/dev/null)

if [ -n "$CERT_EXISTS" ]; then
  echo "Certificate with alias '$CERT_ALIAS' already exists. Deleting the existing certificate."
  /usr/lib/jvm/java-17-openjdk/bin/keytool -delete -cacerts -alias $CERT_ALIAS -storepass "changeit"
fi

# Add the root CA certificate to the cacerts
/usr/lib/jvm/java-17-openjdk/bin/keytool -importcert -cacerts -file $CERT_PATH -alias $CERT_ALIAS -storepass "changeit" -noprompt

echo "Certificate '$CERT_ALIAS' imported successfully."
