# OpenD configuration template

`OpenD.xml.reference` is a sanitized template, not a working account configuration. It contains no login account, password hash or real private-key path.

To use it locally:

1. Copy it to the OpenD configuration location for your installation.
2. Replace `FILL_ME_LOGIN_ACCOUNT` and `FILL_ME_32_HEX_MD5` locally.
3. Set `rsa_private_key` to a private key stored outside this repository, or disable encryption consistently in both OpenD and `.env`.
4. Restrict file permissions and never copy the populated file back into the repository.

Keep the Moomoo SDK and OpenD major versions aligned. Review `.claude/skills/openapi/LEGAL_MooMoo_api_en.md` (or the Chinese version) and the provider's current documentation before use.
