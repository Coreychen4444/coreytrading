# OpenD configuration template

`OpenD.xml.reference` is a sanitized template, not a working account configuration. It contains no login account, password hash or real private-key path.

To use it locally:

1. Copy it to the OpenD configuration location for your installation.
2. Replace `FILL_ME_LOGIN_ACCOUNT` locally.
3. Set `rsa_private_key` to a private key stored outside this repository, or disable encryption consistently in both OpenD and `.env`.
4. Restrict file permissions and never copy the populated file back into the repository.

## Login (OpenD 10.10 and later)

From OpenD 10.10 a password in the configuration file is read but not used for login; OpenD starts at an interactive `Please enter account` prompt. The non-interactive path is: log in once in the OpenD console and choose to remember the password, then start OpenD with `-login_account=<account> -login_by_remember=1`. Do not put a password in the configuration: when a password parameter is present, `login_by_remember` is ignored.

`opend/start.sh` wraps this: it reads the account from the live `OpenD.xml`, checks that the RSA key line is not commented out, and starts OpenD in a detached tmux session named `opend`. Set `OPEND_INSTALL` to the unpacked install directory (default `$HOME/moomoo_OpenD`) or `OPEND_BIN` to the binary itself. If the remembered login expires, or the gateway restarts, OpenD waits at the login prompt and the API is unreachable; attach with `tmux attach -t opend`, log in (remember the password), and detach with `Ctrl-b d`.

## After an OpenD self-update

A self-update can reset the live `OpenD.xml` to the vendor template: the login fields may become placeholders or disappear entirely, and the RSA key line may be commented out, so the API silently stops accepting encrypted connections. Do not detect this by diffing against the shipped default template, because the template itself changes between versions. Check that the fields exist instead:

```bash
grep -cE '^\s*<(login_account|rsa_private_key)>' OpenD.xml   # must be 2
```

If the check fails, restore from your private copy of the populated configuration (or re-apply this template and your local values). Whenever you change the live configuration (port, key path, account), update that private copy in the same step.

Keep the Moomoo SDK and OpenD major versions aligned: upgrade the `moomoo-api` pin in `requirements.txt` in the same change as an OpenD upgrade (patch numbers need not match). Review `.claude/skills/openapi/LEGAL_MooMoo_api_en.md` (or the Chinese version) and the provider's current documentation before use.
