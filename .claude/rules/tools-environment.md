# Tools and environment

## Secrets

- Store local configuration in `.env`; start from `.env.example`.
- Never commit account identifiers, login hashes, tokens, private keys or broker exports.
- `opend/OpenD.xml.reference` is a placeholder template only. Keep the live OpenD configuration outside the repository.

## Python and OpenD

- Use a dedicated virtual environment for the Moomoo SDK because its protobuf constraint may conflict with other tools.
- The OpenD and `moomoo-api` major versions should match.
- RSA is optional in the public example; when enabled, point `FUTU_OPEND_RSA_KEY` at a local absolute path and keep the key outside the repository.
- Broker-connected commands are smoke tests, not part of the offline test suite.

## Time and execution

- Normalize timestamps before applying opening-window, session-close or daylight-saving rules.
- The live pipeline is read/sync/plan only. Low-level order scripts require separate, explicit human approval and are not called by scheduled tasks.
