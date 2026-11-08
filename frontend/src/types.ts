export type CheckState = 'passed' | 'failed' | 'not_run' | 'inconclusive';
export interface AuthUser { id: string; username: string }
export interface AuthSession { user: AuthUser | null; csrf_token: string | null }
export interface GenerationSummary {
  id: string;
  revision_id: string;
  title: string;
  status: Generation['status'];
  created_at: string;
  audit_stale: boolean;
}
export type CheckName = 'code' | 'graph' | 'behavior' | 'xsd' | 'layout' | 'import';
export type Checks = Partial<Record<CheckName, CheckState>>;

export interface Issue {
  code: string;
  category: 'technical' | 'model' | 'question';
  severity: 'error' | 'warning' | 'info';
  message: string;
  element_ids: string[];
  source_quote?: string | null;
  suggested_action?: string | null;
  witness_path?: string[];
}

export interface ClarificationAnswer { question: string; answer: string; source_quote: string }
export interface SemanticReview {
  status: 'needs_review' | 'rules_passed';
  human_review_required: boolean;
  coverage: Array<{ id: string; text: string; start: number; end: number; element_ids: string[]; status: 'linked' | 'unlinked' }>;
  issues: Issue[];
  linked_fragments: number;
  total_fragments: number;
}
export interface SemanticDiff {
  status: 'changed' | 'unchanged' | 'unavailable';
  geometry_only: boolean;
  changes: Array<{ kind: 'added' | 'removed' | 'changed'; element_id: string; name: string; fields: string[];
    before: Record<string, unknown> | null; after: Record<string, unknown> | null }>;
}

export interface Question {
  question: string;
  source_quote: string;
}

export interface ProcessIR {
  source_text: string;
  provenance: Array<{
    element_id: string;
    exact_quote: string;
    origin: 'explicit' | 'structural' | 'proposed';
    source_start?: number | null;
    source_end?: number | null;
  }>;
  assumptions: string[];
  [key: string]: unknown;
}

export interface Generation {
  id: string;
  mode: 'live' | 'mock';
  status: 'ready' | 'needs_clarification' | 'unsupported' | 'error';
  python_code: string | null;
  ir: ProcessIR | null;
  bpmn_xml: string | null;
  checks: Checks;
  issues: Issue[];
  questions: Question[];
  timings: { total_seconds?: number };
  attempt_count: number;
  revision_id: string;
  audit_stale: boolean;
  summary?: string;
  revision_issues?: Issue[];
  source_context?: { description: string; clarification_answers: ClarificationAnswer[] };
  semantic_review?: SemanticReview | null;
  semantic_diff?: SemanticDiff;
}

export interface Validation {
  checks: Checks;
  issues: Issue[];
  semantic_review?: SemanticReview | null;
  semantic_diff?: SemanticDiff;
}

export interface Revision extends Validation {
  id: string;
  revision_id: string;
  audit_stale: boolean;
}

export interface Health {
  status: string;
  mode: 'live' | 'mock';
  generation_available: boolean;
  dependencies: {
    worker: boolean;
    xsd_files_available: boolean;
    llm_configured: boolean;
  };
}

export interface Example {
  id: string;
  title: string;
  text: string;
  expected_status: string;
}

export interface EditorDocument {
  key: string;
  title: string;
  source: string;
  generation: Generation | null;
  xml: string;
  checks: Checks;
  technicalIssues: Issue[];
  importWarnings: string[];
  dirty: boolean;
  stale: boolean;
  semanticReview?: SemanticReview | null;
  semanticDiff?: SemanticDiff;
  editorMode?: 'canvas' | 'document';
  diagramId?: string;
  originalXml?: string;
  canvasNotice?: string;
}
