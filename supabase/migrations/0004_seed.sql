-- 0004 · Seed mínimo (Fase 0)
-- Catálogo de los 13 plugins + org piloto. Los ~151 skills los carga catalog/sync_skills.py (Fase 1).

insert into plugins (key, name, description, version) values
  ('commercial-legal','Commercial Legal','Vendor agreements, NDAs, SaaS subscriptions, renewals.','1.0.0'),
  ('privacy-legal','Privacy Legal','PIAs, DPAs, DSAR responses, policy drift.','1.0.0'),
  ('product-legal','Product Legal','Launch review, marketing claims, feature risk.','1.0.0'),
  ('corporate-legal','Corporate Legal','M&A diligence, board consents, entity compliance.','1.0.0'),
  ('employment-legal','Employment Legal','Hiring/termination review, investigations, policies.','1.0.0'),
  ('regulatory-legal','Regulatory Legal','Reg feeds, policy diffs, comment deadlines.','1.0.0'),
  ('ai-governance-legal','AI Governance Legal','AI inventory, AIA, AI policy, vendor AI review.','1.0.0'),
  ('ip-legal','IP Legal','Clearance, C&D, takedowns, IP clause review.','1.0.0'),
  ('litigation-legal','Litigation Legal','Demand letters, claim charts, chronology, depo prep.','1.0.0'),
  ('law-student','Law Student','Case briefs, IRAC practice, outlines, bar prep.','1.0.0'),
  ('legal-clinic','Legal Clinic','Client letters, intake, memos, court forms.','1.0.0'),
  ('legal-builder-hub','Legal Builder Hub','Skill install/manage/registry.','1.0.0')
on conflict (key) do update set name = excluded.name, description = excluded.description;

insert into orgs (slug, name, kind, plan)
values ('pilot-firm','Pilot Firm','firm','trial')
on conflict (slug) do nothing;
