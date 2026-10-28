import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const partylinksData = volume("partylinks-data", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "europe-west4-drams3a", sizeMB: 50000 });
  const web = service("web", {
    source: github("yanivlevydfs/PartyLinks", { branch: "master", checkSuites: false }),
    replicas: { "europe-west4-drams3a": 1 },
    // Was railway.json (Config as Code, deprecated): same values, field names from the SDK's BuildConfig / DeployConfig.
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    deploy: {
      limitOverride: { containers: { cpu: 7, memoryBytes: 8000000000 } },
      healthcheckPath: "/health",
      healthcheckTimeout: 120,
      // restartPolicyType: ON_FAILURE is Railway's default; it stores it as null, so naming it is a diff forever.
      restartPolicyMaxRetries: 5,
      overlapSeconds: 0,
      drainingSeconds: 30,
    },
    domains: [{ domain: "spotx.fun", port: 8000 }, { domain: "www.spotx.fun", port: 8000 }],
    volumeMounts: { "/app/data": partylinksData },
    env: { PARTYLINKS_ADMIN__TOKEN: preserve(), PARTYLINKS_HTTP__ISRAEL_PROXY: preserve(), PARTYLINKS_HTTP__SOURCE_PROXY: preserve(), PARTYLINKS_SCHEDULER__ENABLED: preserve(), PARTYLINKS_SCHEDULER__RUN_ON_STARTUP: preserve(), PARTYLINKS_SECURITY__TRUSTED_PROXIES: preserve(), PARTYLINKS_SITE__ACCESSIBILITY__COORDINATOR: preserve(), PARTYLINKS_SITE__ACCESSIBILITY__EMAIL: preserve(), PARTYLINKS_SITE__BASE_URL: preserve(), PARTYLINKS_SITE__EMAIL: preserve(), PARTYLINKS_SITE__PRIVACY__EMAIL: preserve(), PORT: preserve(), TZ: preserve() },
  });

  return project("PartyLinks", {
    resources: [web, partylinksData],
  });
});
