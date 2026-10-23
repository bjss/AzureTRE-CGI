# eMudhra certificate mitigation files

The files in this folder are copies of the `revoked-certs` folder in [sonatype/docker-nexus3](https://github.com/sonatype/docker-nexus3/tree/4cfad10cca7e797d1882ded066be7bcc3b4ab8bb/revoked-certs) at commit `4cfad10cca7e797d1882ded066be7bcc3b4ab8bb`.
Sonatype added them for the Nexus 3.97.2 images in [pull request 324](https://github.com/sonatype/docker-nexus3/pull/324) and [pull request 325](https://github.com/sonatype/docker-nexus3/pull/325).
That repository uses the Apache License 2.0. `LICENSE` is a copy of its licence file.

Do not edit these files. To update them, copy the folder again from that repository and change the commit in this file.
Checkstyle does not check this folder (see `.github/linters/checkstyle.xml`), so that the Java file stays the same as the source.

| File | Use in the TRE image |
| --- | --- |
| `revoked-intermediates.sha256` | The SHA-256 fingerprints of the 16 compromised intermediate certificates. The Dockerfile adds them to the JDK `blocked.certs` file. |
| `revoked-intermediates.pem` | The 16 compromised intermediate certificates. The Alpine-based image does not use this file. |
| `distrust-emudhra-roots.sh` | Removes the 4 related root certificates from the JDK `cacerts` file and adds them to `blocked.certs`. It also checks that the OS CA bundle does not contain these roots. |
| `emudhra/EmudhraChainCheck.java` | Fails the image build if the JDK trusts one of the 12 advisory certificate chains or a re-encoded copy of one of their intermediates. |
| `emudhra/emudhra-roots.pem` | The 4 root certificates. |
| `emudhra/chains/` | The certificate chains of the 12 advisory test hosts. |

Remove this folder and the related steps in `../Dockerfile` when one of these conditions is true:

- The Nexus shared service uses a Sonatype image that includes these changes, for example Nexus 3.97.2 or later.
- The JDK and the Alpine packages in the base image block these certificates.
