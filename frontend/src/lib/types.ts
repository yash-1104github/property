export interface PropertyData {
    parcel_number?: string;
    owner_name?: string;
    assessed_value?: number | string;
    taxable_value?: number | string;
    year_built?: number | string;
    lot_size?: string;
    property_address?: string;
    confidence_score?: number; // 0-1 or 0-100
    source_url?: string;
  }
  
  export interface LookupResponse {
    success: boolean;
    data?: PropertyData;
    error?: string;
  }
  