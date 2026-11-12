"use strict";
(() => {
  const page = document.body.dataset.page;
  const view = document.getElementById("view"), notice = document.getElementById("notice");
  let me, scope, csrf, agent, configuration, conversation, pending, generation = 0;
  let streamController, currentCursor, stopped = false, seen = new Set();
  const node = (tag, text, attrs = {}) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
    return element;
  };
  const button = (text, action) => {
    const element = node("button", text, {type: "button"});
    element.addEventListener("click", () => run(action)); return element;
  };
  const message = text => { notice.textContent = text; };
  const scopeQuery = () => new URLSearchParams({owner_id: scope.owner_id, domain: scope.domain});
  const scoped = path => path + "?" + scopeQuery();
  const can = role => scope && scope.roles.includes(role);
  async function api(path, body, signal) {
    const expectedGeneration = generation;
    const options = {credentials: "same-origin", cache: "no-store", redirect: "error", signal};
    if (body !== undefined) {
      options.method = "POST"; options.headers = {"Content-Type": "application/json"};
      if (csrf) options.headers["X-CSRF-Token"] = csrf;
      options.body = JSON.stringify(body);
    }
    let response;
    try { response = await fetch(path, options); }
    catch (error) { if (error.name === "AbortError") throw error; throw {code: "unavailable", status: 503}; }
    const value = await response.json();
    if (generation !== expectedGeneration) throw new DOMException("Workspace changed", "AbortError");
    if (!response.ok) throw {code: value.code, status: response.status};
    return value;
  }
  function clearPrivate() {
    generation++; document.getElementById("preview").close();
    streamController?.abort();
    view.replaceChildren(); document.getElementById("scope-bar").hidden = true;
    document.getElementById("roles").textContent = "";
    document.getElementById("scope").replaceChildren();
    document.getElementById("account").replaceChildren();
  }
  function problem(error) {
    if (error.name === "AbortError") return;
    if (error.status === 401) {
      stopped = true; clearPrivate(); message("Your session has expired. Sign in again to continue.");
      view.append(node("a", "Sign in", {href: "/app/sign-in"})); return;
    }
    const text = {403: "Your current permissions do not allow this action. Reload your workspace.",
      404: "This item is unavailable in the selected workspace.",
      409: error.code === "resync-required" ? "Activity needs to be reloaded before updates can resume." : "The item changed. Reload it and review the change again.",
      413: "The request is too large.", 429: "A usage limit was reached. Wait before trying again.",
      400: "Check the input and try again.", 503: "The service is unavailable. No completion is assumed; retry the same pending request."};
    message(text[error.status] || "The request could not be completed.");
    if (error.status === 403 || error.status === 503) {
      clearPrivate(); view.append(button("Reload workspace", initialize));
    }
  }
  async function run(action) { try { await action(); } catch (error) { problem(error); } }
  function field(form, label, name, type = "text", max = 256) {
    const id = "field-" + name;
    const input = node(type === "textarea" ? "textarea" : "input", undefined, {id, name, maxlength: String(max), required: ""});
    if (type !== "textarea") input.type = type;
    form.append(node("label", label, {for: id}), input); return input;
  }
  function review(title, details, apply) {
    const dialog = document.getElementById("preview"), content = document.getElementById("preview-body");
    document.getElementById("preview-title").textContent = title;
    content.replaceChildren();
    const list = node("dl");
    for (const [label, value] of details) list.append(node("dt", label), node("dd", String(value)));
    content.append(list);
    document.getElementById("preview-cancel").onclick = () => dialog.close();
    document.getElementById("preview-apply").onclick = async () => {
      dialog.close(); await run(apply);
    };
    dialog.showModal();
  }
  async function signIn() {
    const flow = await api("/auth/login", {schema_version: 1});
    const source = new URL(flow.authorization_url), target = new URL("/app/authorize", location.origin);
    for (const key of ["state", "nonce", "code_challenge"]) target.searchParams.set(key, source.searchParams.get(key));
    target.searchParams.set("mode", "browser"); location.assign(target);
  }
  if (page === "sign-in") {
    document.getElementById("begin-login").onclick = () => run(signIn); return;
  }
  if (page === "authorize") {
    const form = document.getElementById("authorize");
    form.addEventListener("submit", event => { event.preventDefault(); run(async () => {
      const values = Object.fromEntries(new FormData(form));
      try {
        const value = await api("/auth/fixture/authorize", {schema_version: 1, ...values});
        if (value.mode === "browser") {
          if (!value.callback_path.startsWith("/auth/callback?")) throw {status: 400};
          location.assign(value.callback_path);
        } else {
          form.hidden = true; const result = document.getElementById("authorization-result");
          result.append(node("h2", "Finish CLI sign-in"), node("p", "Paste this one-use code into the waiting terminal. It expires shortly."),
            node("pre", value.code, {id: "cli-code", tabindex: "0"}));
        }
      } finally { form.elements.credential.value = ""; }
    }); }); return;
  }
  async function initialize() {
    generation++;
    stopped = false; streamController?.abort(); currentCursor = undefined; seen = new Set();
    me = await api("/api/v1/me"); csrf = me.csrf_token;
    const select = document.getElementById("scope"), account = document.getElementById("account");
    account.replaceChildren(button("Renew session", async () => { await api("/api/v1/session/refresh", {schema_version: 1}); message("Session renewed within its original maximum lifetime."); }), button("Sign out", async () => {
      try { await api("/api/v1/session/logout", {schema_version: 1}); }
      finally { clearPrivate(); location.assign("/app/sign-in"); }
    }));
    select.replaceChildren();
    for (const membership of me.memberships) select.append(node("option", membership.domain + " · " + membership.owner_id.slice(0, 8), {value: membership.owner_id + ":" + membership.domain}));
    if (!me.memberships.length) {
      view.replaceChildren(node("p", "No workspace access has been assigned to this account."));
      document.getElementById("scope-bar").hidden = true; return;
    }
    const previous = scope && me.memberships.find(m => m.owner_id === scope.owner_id && m.domain === scope.domain);
    scope = previous || me.memberships[0]; select.value = scope.owner_id + ":" + scope.domain;
    document.getElementById("scope-bar").hidden = false;
    select.onchange = () => run(async () => {
      if (pending) { select.value = scope.owner_id + ":" + scope.domain; message("Retry the pending send before changing workspaces."); return; }
      generation++; document.getElementById("preview").close();
      streamController?.abort(); pending = undefined; conversation = undefined; currentCursor = undefined; seen = new Set();
      scope = me.memberships.find(m => m.owner_id + ":" + m.domain === select.value);
      await load();
    });
    for (const anchor of document.querySelectorAll("nav a")) if (anchor.pathname.endsWith("/" + page)) anchor.setAttribute("aria-current", "page");
    await load();
  }
  async function load() {
    streamController?.abort(); message("");
    document.getElementById("roles").textContent = scope.roles.join(", ");
    view.replaceChildren(node("p", "Loading…"));
    const agents = await api(scoped("/api/v1/agents")); agent = agents[0];
    if (page === "conversations") await conversations();
    else if (page === "agents") await agentsPage();
    else if (page === "configuration") await configurationPage();
    else if (page === "activity") {
      view.replaceChildren(node("p", "Committed workspace activity. Showing the latest 100 loaded entries."), node("div", undefined, {id: "activity-items"}));
      const first = await api(scoped("/api/v1/activity") + "&limit=100");
      first.items.forEach(addActivity); currentCursor = first.next_cursor;
      watch();
    } else if (page === "approvals") {
      const value = await api(scoped("/api/v1/approvals"));
      view.replaceChildren(node("p", value.available ? "Approval records" : "Approvals are unavailable in this test release."));
      const disabled = button("Approve", async () => {}); disabled.disabled = true;
      disabled.setAttribute("aria-describedby", "approval-note");
      view.append(node("p", "No tool dispatch or effect can be authorized here.", {id: "approval-note"}), disabled);
    }
  }
  async function conversations() {
    const result = await api(scoped("/api/v1/conversations") + "&limit=100");
    const chooser = node("select", undefined, {id: "conversation"});
    view.replaceChildren(node("label", "Conversation", {for: "conversation"}), chooser);
    for (const item of result.items) {
      const value = item.conversation;
      chooser.append(node("option", new Date(value.created_at).toLocaleString() + " · " + value.conversation_id.slice(0, 8), {value: value.conversation_id}));
    }
    if (!conversation || !result.items.some(x => x.conversation.conversation_id === conversation)) conversation = result.items[0]?.conversation.conversation_id;
    if (conversation) chooser.value = conversation;
    chooser.onchange = () => run(async () => { if (pending) { message("Retry the pending send before changing conversations."); chooser.value = conversation; return; } conversation = chooser.value; await showMessages(); });
    if (can("operator")) view.append(button("New conversation", async () => {
      if (pending) { message("Retry the pending send first."); return; }
      const id = crypto.randomUUID(), key = "conversation:" + id;
      await api(scoped("/api/v1/conversations"), {schema_version: 1, domain: scope.domain, conversation_id: id, operation_key: key});
      conversation = id; await conversations();
    }));
    if (!result.items.length) view.append(node("p", "No conversations yet."));
    view.append(node("div", undefined, {id: "messages"}));
    if (conversation) await showMessages();
  }
  async function showMessages() {
    const selectedConversation = conversation;
    const region = document.getElementById("messages"); region.replaceChildren();
    const result = await api(scoped("/api/v1/conversations/" + conversation + "/messages") + "&limit=100");
    if (selectedConversation !== conversation || !region.isConnected) return;
    for (const item of result.items) {
      const article = node("article"); article.append(node("h2", item.author_kind === "human" ? "Workspace member" : "Test agent"), node("p", item.body, {class: "message"}));
      if (item.author_kind === "human") article.append(button("Check request status", async () => {
        const path = "/api/v1/conversations/" + item.conversation_id + "/messages/" + item.message_id;
        const state = await api(scoped(path + "/status"));
        article.append(node("p", "Request state: " + state.state + (state.reason ? " (" + state.reason + ")" : ""), {role: "status"}));
        if (state.can_cancel) article.append(button("Review cancellation", async () => {
          review("Cancel this request", [["Message", item.body], ["Current state", state.state]], async () => {
            const result = await api(scoped(path + "/cancel"), {schema_version: 1});
            await showMessages(); message("Request state: " + result.state);
          });
        }));
      }));
      region.append(article);
    }
    if (!result.items.length) region.append(node("p", "This conversation is empty."));
    region.append(button("Refresh messages", showMessages));
    if (!can("operator")) { region.append(node("p", "You can read this conversation. Sending requires the operator role.")); return; }
    if (!agent?.enabled) { region.append(node("p", "The test agent is unavailable. An administrator must enable it and review its configuration.")); return; }
    const form = node("form"), text = field(form, "Message", "message", "textarea", 16384);
    if (pending) text.value = pending.text;
    const send = node("button", pending ? "Retry pending send" : "Send message", {type: "submit"}); form.append(send);
    text.readOnly = !!pending;
    form.onsubmit = event => { event.preventDefault(); run(async () => {
      if (!pending) {
        if (!text.value.trim() || new TextEncoder().encode(text.value).length > 16384) { message("Enter a message of at most 16 KiB."); return; }
        pending = {schema_version: 1, domain: scope.domain, conversation_id: conversation, operation_key: "message:" + crypto.randomUUID(), agent_id: "control-test-agent", expected_agent_revision: agent.manifest.revision, text: text.value};
      }
      text.readOnly = true; send.disabled = true;
      try {
        const receipt = await api(scoped("/api/v1/conversations/" + conversation + "/messages"), pending);
        pending = undefined; message("Message stored. Waiting for the test agent."); await showMessages();
        pollRun(receipt);
      } catch (error) {
        if (error.status && error.status !== 503) pending = undefined;
        throw error;
      } finally { send.disabled = false; send.textContent = pending ? "Retry pending send" : "Send message"; text.readOnly = !!pending; }
    }); }; region.append(form);
  }
  async function pollRun(receipt) {
    const selectedConversation = conversation, expectedGeneration = generation;
    for (let i = 0; i < 30 && !stopped && document.visibilityState === "visible"; i++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      if (expectedGeneration !== generation || selectedConversation !== conversation) return;
      try {
        const path = "/api/v1/conversations/" + selectedConversation + "/messages/" + receipt.message_id;
        const value = await api(scoped(path + "/status"));
        if (expectedGeneration !== generation || selectedConversation !== conversation) return;
        message("Message state: " + value.state + (value.reason ? " (" + value.reason + ")" : ""));
        if (["succeeded", "failed", "cancelled"].includes(value.state)) { await showMessages(); return; }
      } catch (error) { problem(error); return; }
    }
    message("The request remains stored. Refresh messages to check its result.");
  }
  async function agentsPage() {
    const health = await api(scoped("/api/v1/status")); view.replaceChildren();
    if (!agent) {
      view.append(node("p", "No test agent is registered."));
      if (can("administrator")) {
        const form = node("form"), description = field(form, "Agent description", "description", "text", 512);
        form.append(node("button", "Review registration", {type: "submit"}));
        form.onsubmit = event => { event.preventDefault(); const body = {schema_version: 1, owner_id: scope.owner_id, domain: scope.domain, agent_id: "control-test-agent", revision: 1, description: description.value}; review("Register disabled test agent", [["Description", body.description], ["Initial state", "Disabled"], ["Model and tools", "None"]], async () => {
          await api("/api/v1/agents", body); await load();
        }); }; view.append(form);
      } return;
    }
    view.append(node("h2", agent.manifest.description), node("p", "Revision " + agent.manifest.revision + " · " + (agent.enabled ? "Enabled" : "Disabled")));
    if (!health.services.length) view.append(node("p", "Health is unknown: no current observation."));
    for (const item of health.services) view.append(node("p", item.service_id + " · " + item.state + " · " + new Date(item.seen_at).toLocaleString()));
    if (can("administrator")) {
      const form = node("form"), reason = field(form, "Reason for state change", "reason");
      form.append(node("button", agent.enabled ? "Review disable" : "Review enable", {type: "submit"}));
      form.onsubmit = event => { event.preventDefault(); const body = {schema_version: 1, expected_revision: agent.manifest.revision, enabled: !agent.enabled, reason: reason.value};
        review("Change test agent state", [["Expected revision", body.expected_revision], ["New state", body.enabled ? "Enabled" : "Disabled"], ["Reason", body.reason]], async () => {
          await api(scoped("/api/v1/agents/control-test-agent/state"), body); await load(); message("State changed. Review a new configuration revision before sending messages.");
        }); }; view.append(form);
    }
  }
  async function configurationPage() {
    configuration = await api(scoped("/api/v1/configuration")); view.replaceChildren();
    view.append(node("p", configuration ? "Current configuration revision " + configuration.revision : "No configuration has been reviewed."));
    if (configuration) {
      const details = node("dl");
      for (const [label, value] of [["Agent revision", configuration.agent.revision], ["Description", configuration.agent.description], ["Reason", configuration.reason], ["Model and tools", "None"]]) details.append(node("dt", label), node("dd", String(value)));
      view.append(details);
    }
    if (!agent || !can("administrator")) { view.append(node("p", "An administrator and a registered test agent are required to change configuration.")); return; }
    const form = node("form"), reason = field(form, "Reason for configuration revision", "reason");
    form.append(node("button", "Review configuration", {type: "submit"}));
    form.onsubmit = event => { event.preventDefault(); const body = {schema_version: 1, owner_id: scope.owner_id, domain: scope.domain, revision: (configuration?.revision || 0) + 1, previous_revision: configuration?.revision || 0, agent: agent.manifest, reason: reason.value};
      review("Apply configuration revision " + body.revision, [["Workspace", scope.domain], ["Previous revision", body.previous_revision], ["Agent revision", body.agent.revision], ["Description", body.agent.description], ["Reason", body.reason], ["Model and tools", "None"]], async () => { await api("/api/v1/configuration/revisions", body); await load(); message("Configuration revision applied."); });
    }; view.append(form);
  }
  function addActivity(item) {
    if (seen.has(item.activity_id)) return; seen.add(item.activity_id);
    const target = document.getElementById("activity-items"); if (!target) return;
    const card = node("article"); card.append(node("h2", item.kind), node("p", new Date(item.recorded_at).toLocaleString()), node("p", "Conversation " + item.conversation_id));
    target.append(card); while (target.children.length > 100) target.firstElementChild.remove();
    if (seen.size > 200) seen = new Set([item.activity_id]);
  }
  async function watch() {
    streamController?.abort(); const controller = new AbortController(); streamController = controller;
    for (let attempt = 0; attempt < 3 && !controller.signal.aborted && !stopped; attempt++) {
      try {
        const headers = currentCursor ? {"Last-Event-ID": currentCursor} : {};
        const response = await fetch(scoped("/api/v1/activity/stream"), {headers, credentials: "same-origin", cache: "no-store", redirect: "error", signal: controller.signal});
        if (!response.ok) { const error = await response.json(); throw {status: response.status, code: error.code}; }
        const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = "";
        while (true) {
          const value = await reader.read(); if (value.done) break;
          buffer += decoder.decode(value.value, {stream: true});
          let boundary;
          while ((boundary = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
            if (frame.length > 8192) throw {status: 409, code: "resync-required"};
            let id, data, kind;
            for (const line of frame.split("\n")) {
              if (line.startsWith("id: ")) id = line.slice(4);
              if (line.startsWith("data: ")) data = line.slice(6);
              if (line.startsWith("event: ")) kind = line.slice(7);
            }
            if (kind === "resync-required") throw {status: 409, code: "resync-required"};
            if (kind === "activity" && data && id) { addActivity(JSON.parse(data)); currentCursor = id; }
          }
          if (buffer.length > 8192) throw {status: 409, code: "resync-required"};
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        if ([400, 401, 403, 409, 429].includes(error.status)) { problem(error); return; }
      }
      await new Promise(resolve => setTimeout(resolve, 1000 * (attempt + 1)));
    }
    if (!controller.signal.aborted) message("Activity updates paused. Reload activity to reconnect.");
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { streamController?.abort(); clearPrivate(); }
    else run(initialize);
  });
  setInterval(() => { if (!document.hidden && !stopped) run(async () => {
    const current = await api("/api/v1/me");
    if (JSON.stringify(current.memberships) !== JSON.stringify(me?.memberships)) await initialize();
  }); }, 10000);
  run(initialize);
})();
