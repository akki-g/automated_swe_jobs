export type RoleType = "new_grad" | "intern";

export type TargetField =
  | "software_engineering"
  | "data_science_analytics"
  | "product_management"
  | "finance_investment_banking"
  | "consulting"
  | "marketing"
  | "sales"
  | "operations"
  | "design";

export interface User {
  id: number;
  name: string;
  email: string;
  phone: string | null;
  profile_completed: boolean;
  email_digest_enabled: boolean;
  email_digest_time: string;
}

export interface ResumeProfile {
  skills?: string[];
  past_titles?: string[];
  experience_level?: string;
  years_experience?: number | null;
  inferred_target_fields?: TargetField[];
  education_fields?: string[];
  summary?: string;
}

export interface Profile {
  name: string;
  email: string;
  role_types: RoleType[];
  target_fields: TargetField[];
  keywords: string[];
  locations: string[];
  sponsorship_required: boolean | null;
  freeform_notes: string;
  resume_profile: ResumeProfile;
  resume_updated_at: string | null;
  email_digest_enabled: boolean;
  email_digest_time: string;
  profile_completed: boolean;
}

export type Priority = "high" | "normal";

export interface MatchItem {
  id: number;
  company: string;
  title: string;
  url: string;
  location: string | null;
  role_type: RoleType | null;
  posted_at: string | null;
  score: number;
  blurb: string;
  priority: Priority;
  matched_target_field: TargetField | null;
  saved: boolean;
  created_at: string;
  is_new: boolean;
}

export interface MatchListResponse {
  items: MatchItem[];
  total: number;
}

export interface MatchFilters {
  company?: string;
  location?: string;
  target_field?: TargetField;
  priority?: Priority;
  min_score?: number;
  saved?: boolean;
  new_only?: boolean;
}
