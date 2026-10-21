// Passkeys (WebAuthn) im Browser: Optionen holen, Schlüssel fragen, Antwort an den Server.
// Nur Umwandlung zwischen JSON und ArrayBuffer; geprüft wird alles im Server (py_webauthn).
(() => {
  "use strict";

  const toBuffer = (value) => {
    const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "===".slice((base64.length + 3) % 4);
    return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0)).buffer;
  };

  const toBase64url = (buffer) => {
    let text = "";
    new Uint8Array(buffer).forEach((byte) => { text += String.fromCharCode(byte); });
    return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  };

  const csrfToken = () => document.querySelector('meta[name="csrf-token"]').content;

  const post = async (url, body) => {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "Der Server hat die Anfrage abgelehnt.");
    }
    return data;
  };

  const creationOptions = (options) => {
    options.challenge = toBuffer(options.challenge);
    options.user.id = toBuffer(options.user.id);
    (options.excludeCredentials || []).forEach((c) => { c.id = toBuffer(c.id); });
    return options;
  };

  const requestOptions = (options) => {
    options.challenge = toBuffer(options.challenge);
    (options.allowCredentials || []).forEach((c) => { c.id = toBuffer(c.id); });
    return options;
  };

  const registrationJSON = (credential) => ({
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(credential.response.clientDataJSON),
      attestationObject: toBase64url(credential.response.attestationObject),
      transports: credential.response.getTransports ? credential.response.getTransports() : [],
    },
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment,
  });

  const authenticationJSON = (credential) => ({
    id: credential.id,
    rawId: toBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: toBase64url(credential.response.clientDataJSON),
      authenticatorData: toBase64url(credential.response.authenticatorData),
      signature: toBase64url(credential.response.signature),
      userHandle: credential.response.userHandle ? toBase64url(credential.response.userHandle) : null,
    },
    clientExtensionResults: credential.getClientExtensionResults(),
    authenticatorAttachment: credential.authenticatorAttachment,
  });

  const run = async (button) => {
    if (!window.PublicKeyCredential) {
      throw new Error("Dieser Browser unterstützt keine Passkeys.");
    }
    let body;
    if (button.dataset.passkey === "login") {
      const options = await post(button.dataset.optionsUrl);
      const credential = await navigator.credentials.get({ publicKey: requestOptions(options) });
      body = { credential: authenticationJSON(credential) };
    } else {
      // Mit freigegebenem YubiKey (#95): zuerst ihn bestätigen, dann den neuen Schlüssel.
      let confirmation = null;
      if (button.dataset.yubikey) {
        const path = new URL(button.dataset.submitUrl, window.location.href).pathname;
        const confirmOptions = await post(button.dataset.yubikey, { path });
        const yubikey = await navigator.credentials.get({ publicKey: requestOptions(confirmOptions) });
        confirmation = authenticationJSON(yubikey);
      }
      const options = await post(button.dataset.optionsUrl);
      const credential = await navigator.credentials.create({ publicKey: creationOptions(options) });
      const nameInput = document.getElementById(button.dataset.nameInput || "");
      body = {
        credential: registrationJSON(credential),
        name: nameInput ? nameInput.value : "",
        yubikey_assertion: confirmation,
      };
    }
    const result = await post(button.dataset.submitUrl, body);
    window.location.assign(result.redirect);
  };

  // Nutzerverwaltung (#95): Vor dem Absenden die Änderung mit dem YubiKey bestätigen. Die
  // Bestätigung gilt nur für diese eine Anfrage an genau diese Adresse.
  const confirmForm = async (form) => {
    if (!window.PublicKeyCredential) {
      throw new Error("Dieser Browser unterstützt keine Passkeys.");
    }
    const path = new URL(form.action, window.location.href).pathname;
    const options = await post(form.dataset.yubikey, { path });
    const credential = await navigator.credentials.get({ publicKey: requestOptions(options) });
    let field = form.querySelector('input[name="yubikey_assertion"]');
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "yubikey_assertion";
      form.appendChild(field);
    }
    field.value = JSON.stringify(authenticationJSON(credential));
    form.submit();
  };

  document.addEventListener("submit", (event) => {
    const form = event.target.closest("form[data-yubikey]");
    if (!form) {
      return;
    }
    event.preventDefault();
    const status = document.getElementById("yubikey-status");
    const buttons = form.querySelectorAll("button");
    buttons.forEach((button) => { button.disabled = true; });
    if (status) {
      status.textContent = "Bitte die Änderung mit dem YubiKey bestätigen …";
    }
    confirmForm(form).catch((error) => {
      if (status) {
        status.textContent = error.name === "NotAllowedError"
          ? "Abgebrochen oder nicht erlaubt. Nichts geändert."
          : error.message;
      }
      buttons.forEach((button) => { button.disabled = false; });
    });
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-passkey]");
    if (!button) {
      return;
    }
    event.preventDefault();
    const status = document.getElementById("passkey-status");
    button.disabled = true;
    status.textContent = "Bitte den Schlüssel bestätigen …";
    run(button).catch((error) => {
      status.textContent = error.name === "NotAllowedError"
        ? "Abgebrochen oder nicht erlaubt."
        : error.message;
      button.disabled = false;
    });
  });
})();
