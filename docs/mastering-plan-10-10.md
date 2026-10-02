# SACM Mastering Plan — droga do 10/10

## Pozycjonowanie, którego bronimy

SACM nie jest edytorem kodu ani pojedynczym autonomicznym programistą. Jest
**audit-native, policy-driven control plane dla dostarczania oprogramowania
przez wiele agentów**. Cursor, Codex, Devin i agenci własni są wykonawcami;
SACM zapewnia im właściwy kontekst, ograniczenia, dowody i rozliczalność.

## Definicja 10/10

Wynik 10/10 oznacza, że firma może bezpiecznie przeprowadzić zmianę od ticketu
do draft PR, a niezależny reviewer może odtworzyć: intencję, kontekst,
decyzje, wykonawców, użyte narzędzia, wynik testów, polityki i dokładne zmiany
w Git. System nie deklaruje sukcesu bez weryfikowalnych dowodów.

| Wymiar | Bramka 10/10 | Miernik sukcesu |
| --- | --- | --- |
| Dostarczenie | Jira → plan → wykonanie → evidence → draft PR | >=95% kwalifikowanych zadań przechodzi bez ręcznego przepisywania kontekstu |
| Wiarygodność | Każdy wynik ma hash, provenance i weryfikację | 100% ukończonych runów z Evidence Pack i kompletną traceability |
| Bezpieczeństwo | Polityki są fail-closed i sekretów nie ma w pamięci | 0 sekretów w logach/pakietach; 100% blokad policyjnych audytowalnych |
| Cognitive state | Stan da się odtworzyć dla historycznego commita | >=99% indeksowanych commitów ma requirement/agent/file/test provenance |
| Jakość agentów | Akceptacja oparta na wynikach, nie deklaracji modelu | mierzone pass@1, acceptance rate, rollback rate i koszt zaakceptowanej zmiany |
| Operacyjność | Produkcyjna, obserwowalna instalacja | SLO API 99.9%, recovery drills, RPO/RTO oraz dashboard SRE |
| Doświadczenie | Review trwa minuty, nie dni | czas od PR do decyzji i czas znalezienia „why chain” mierzone co tydzień |

## Etap 1 — Jeden nieskazitelny przepływ (0–30 dni)

Cel: produkt demonstracyjny i wdrażalny dla jednego repozytorium.

1. Ustalić referencyjny scenariusz: Jira ticket z kryteriami akceptacji,
   repozytorium, polityka `strict`, agent kodujący, tester, security reviewer
   i draft PR — bez automatycznego merge.
2. Każda finalizacja musi utworzyć Evidence Pack, odświeżyć traceability i
   utworzyć FEATURE snapshot Cognitive State. Ta integracja jest wdrażana w
   bieżącej zmianie.
3. Dodać Playwright/API E2E na PostgreSQL, Redis i lokalnym GitHub sandboxie;
   SQLite pozostaje wyłącznie szybkim testem jednostkowym.
4. Dashboard ma pokazywać jedną stronę „delivery passport”: wymagania,
   decyzje, commit/diff, testy, polityki, evidence i draft PR.
   Dostępny jest już stabilny odczyt API:
   `GET /v1/projects/{project_id}/cognitive/deliveries/{delivery_id}/passport`.
5. Ustawić baseline telemetryczny: czas przebiegu, tokeny, koszt, retry,
   blokady polityk i wynik weryfikacji.

**Exit gate:** powtarzalny demo-run od ticketu do draft PR, signed evidence
oraz link do snapshotu, z testem regresji dla każdej bramki.

## Etap 2 — Zaufany Cognitive State (30–60 dni)

1. Automatycznie indeksować commity z kolejki Git po każdym pushu i wiązać je
   najpierw jawnie przez task/branch/metadata, a dopiero potem semantycznie.
2. Dodać parser symboli (SCIP lub tree-sitter), dependency graph i relacje
   symbol → test → requirement. Każda niepewna relacja musi mieć źródło oraz
   confidence; nie może udawać faktu.
3. Zaimplementować stale-memory i architectural-drift jako alerty z dowodem,
   nie jako automatyczne zmiany kodu.
4. Ustawić retencję, PII/secret scanning przed embeddingiem oraz testy
   temporalne: odpowiedź „przed commitem X” nigdy nie może przeciekać z
   przyszłości.

**Exit gate:** historyczne `why`, impact i context reconstruction działają na
trzech realnych repozytoriach oraz są ręcznie sprawdzone na próbie commitów.

## Etap 3 — Enterprise execution plane (60–90 dni)

1. Dostarczyć Helm/Terraform, referencyjną konfigurację Postgres/Redis/object
   storage, OIDC i rotację sekretów.
2. Przetestować customer-managed executor, podpisane joby, izolację tenantów,
   network egress policy oraz awarię kolejki/bazy.
3. Ustanowić SLO, alerty, runbooki, backup/restore i regularne recovery drills.
4. Włączyć compliance export dla Evidence Pack, audit chain i polityk.

**Exit gate:** niezależny deployment w środowisku testowym klienta przechodzi
security review i odtwarza run po kontrolowanej awarii.

## Etap 4 — Przewaga mierzalna nad narzędziami coding-agent (90–180 dni)

1. Uruchomić jawny benchmark: porównać pojedynczego agenta z SACM-sterowanym
   przepływem na tych samych zadaniach, repozytoriach i budżetach.
2. Publikować acceptance rate, medianę lead time, koszt zaakceptowanego PR,
   post-merge defect/rollback rate i completeness evidence — z metodologią.
3. Rozwinąć adaptery: Codex, Cursor background agents, Devin i wewnętrzne
   workery mogą działać jako wymienialni wykonawcy z tym samym kontraktem.
4. Wprowadzić consensus dla decyzji wysokiego ryzyka, gdzie zatwierdzenie
   bezpieczeństwa i człowieka pozostaje obowiązkowe.

**Exit gate:** SACM wykazuje lepszy koszt skorygowany ryzykiem lub krótszy czas
review niż niezarządzany agent, bez pogorszenia jakości.

## Kolejność implementacji

1. Delivery-to-cognitive bridge i FEATURE snapshot. **W toku.**
2. Pełny E2E PostgreSQL/Redis/GitHub sandbox.
3. Widok delivery passport z cognitive evidence.
4. Symbol/dependency indexing i stale-memory.
5. Instalacja enterprise oraz SLO/recovery.
6. Benchmark i publiczna metodologia wyników.

## Zasady, których nie łamiemy

- Git jest źródłem prawdy o kodzie; vector memory nie jest bazą relacji.
- Dowody są append-only; brak dowodu oznacza `UNKNOWN` albo blokadę, nie
  pozytywną deklarację modelu.
- Draft PR może być automatyczny; merge, release i wyjątkowe uprawnienia nie.
- Żaden sekret, token ani prywatny klucz nie trafia do pamięci semantycznej.
- Każdy nowy automatyzm ma idempotency key, test negatywny i obserwowalny ślad.
