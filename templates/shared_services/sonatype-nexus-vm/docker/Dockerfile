FROM sonatype/nexus3:3.94.0

# Apply the eMudhra certificate mitigations of the Sonatype Nexus 3.97.2 image to Nexus 3.94.0.
# Nexus 3.97 and later need required base telemetry, so TRE stays on 3.94.0.
# The steps are the same as in Dockerfile.alpine.java21 of sonatype/docker-nexus3
# (pull requests 324 and 325). See revoked-certs/README.md for when to remove them.
USER root

# Alpine ca-certificates 20261020-r0 removes the compromised eMudhra roots from the OS bundle
# (ca-certificates-bundle) and from the source of the Java cacerts (ca-certificates).
RUN apk add --no-cache --upgrade "ca-certificates-bundle>=20261020-r0" "ca-certificates>=20261020-r0"

# Block the 16 compromised intermediates and the 4 roots in the JDK. The JDK then rejects these
# certificates from every trust store. This includes the Nexus truststore.
# EmudhraChainCheck fails the build if this JDK trusts one of the advisory chains.
COPY revoked-certs/ /tmp/emudhra-build/
RUN set -e; \
    jh=/usr/lib/jvm/default-jvm; \
    f=$(readlink -f "$jh/lib/security/blocked.certs"); \
    [ -f "$f" ]; \
    grep -v '^#' /tmp/emudhra-build/revoked-intermediates.sha256 > /tmp/emudhra-build/intermediates; \
    echo >> "$f"; \
    cat /tmp/emudhra-build/intermediates >> "$f"; \
    while read -r h; do grep -qx "$h" "$f"; done < /tmp/emudhra-build/intermediates; \
    sh /tmp/emudhra-build/distrust-emudhra-roots.sh jre "$jh" "$(readlink -f "$jh/lib/security/cacerts")"; \
    sh /tmp/emudhra-build/distrust-emudhra-roots.sh check "$jh" /etc/ssl/certs/ca-certificates.crt; \
    "$jh/bin/java" /tmp/emudhra-build/emudhra/EmudhraChainCheck.java \
        /tmp/emudhra-build/emudhra/chains /tmp/emudhra-build/emudhra/emudhra-roots.pem; \
    rm -rf /tmp/emudhra-build

USER nexus
