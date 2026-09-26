Subject: SMYA Final Submission - PCNEI7FK

Dear SMYA organisers,

Please find below the final submission for team PCNEI7FK.

Team Code: PCNEI7FK

Problem Statement: Supplier Comparison
Our solution: ProcureAI — The Autonomous Procurement War Room. An AI procurement team (four agents on OpenClaw, running on AWS Lightsail with Claude Sonnet 4.5 on Amazon Bedrock) that ingests supplier quotations in PDF, Excel and email form, extracts and validates them, scores suppliers on cost, lead time, reliability and risk, negotiates within strict boundaries, replans when requirements change mid-run, and presents a recommendation for human approval before any purchase order is generated. Every number is computed deterministically outside the model, and every consequential action requires a human click.

GitHub Repository: https://github.com/gaamiranda/hackathon
(README.md is the entry point; PLAN.md is the plan of record with every design decision.)

Business Proposal: attached, ProcureAI_Business_Proposal.pdf

Technical Document: attached, ProcureAI_Technical_Document.pdf

Demo Video: https://www.youtube.com/watch?v=QoNal5Gtsxg

Deployment Evidence:
- Live system: http://47.129.120.76/ (AWS Lightsail, ap-southeast-1, OpenClaw agent runtime per the starter kit)
- Health endpoint: http://47.129.120.76/api/health
- A finished live run with a generated purchase order: http://47.129.120.76/runs/run-75268093
- Details, health output at capture time and screenshots: attached ProcureAI_Deployment_Evidence.pdf (also at docs/submission/DEPLOYMENT_EVIDENCE.md in the repository)

Thank you for organising the hackathon and for the OpenClaw and Bedrock infrastructure.

Best regards,
Gonçalo Miranda
Team PCNEI7FK
