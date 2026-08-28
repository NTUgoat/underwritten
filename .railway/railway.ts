// Railway Infrastructure as Code.
//
// This replaces railway.json, which was deleted rather than kept. Railway's own
// documentation states that Config as Code is deprecated, that "new services
// cannot opt into Config as Code", and that existing files stop being read on
// 2026-12-01. A railway.json in this repository would have been read by nobody
// while looking like working configuration - the healthcheck would simply never
// have been set, silently. Dead config that looks live is worse than none.
//
// Build and start are deliberately absent here. The Dockerfile is the single
// definition of how this process starts, so the container that runs on Railway
// is the same one that runs locally.
//
// Note: the IaC DSL exposes healthcheck and healthcheckTimeout but has no
// restart-policy field, so the restart policy that railway.json used to carry
// cannot be expressed in code. It must be set in the Railway dashboard if it is
// wanted. See docs/DEPLOY.md.
//
// Apply with:  npm install railway && railway link && railway config apply

import { defineRailway, project, service } from "railway/iac";

export default defineRailway(() => {
  const web = service("underwritten", {
    healthcheck: "/healthz",
    healthcheckTimeout: 120,
  });

  return project("underwritten", {
    resources: [web],
  });
});
