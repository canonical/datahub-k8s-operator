#!/bin/sh

# Variables
PEM_FILE="/ca-cert.pem"
KEYSTORE="/truststore.p12"
STORE_TYPE="PKCS12"

# Split the PEM file into individual certificates
csplit -f /tmp/opensearch_crt_ -b %02d.pem $PEM_FILE '/^-----BEGIN CERTIFICATE-----$/' '{*}'

# We expect three files:
#   1. empty file
#   2. tls certificate for opensearch
#   3. root certificate for the second certificate
# We only import the last two
/usr/lib/jvm/java-17-openjdk/bin/keytool -importcert -file /tmp/opensearch_crt_01.pem -alias opensearch-ca -keystore $KEYSTORE -storepass $STORE_PASS -storetype $STORE_TYPE -noprompt
/usr/lib/jvm/java-17-openjdk/bin/keytool -importcert -cacerts -file /tmp/opensearch_crt_02.pem -alias opensearch-root-ca -storepass "changeit" -noprompt
