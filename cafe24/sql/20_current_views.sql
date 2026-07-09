-- _d0(당일 15분 갱신) + 본표(어제까지 확정) 통합 "current" 뷰.
-- 과거 날짜는 본표에서, 오늘(KST)은 _d0에서 → 항상 최신 상태를 한 곳에서 조회.
-- 스키마가 동일한 테이블쌍이라 SELECT * UNION ALL 사용.

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_sales_daily_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_sales_daily`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_sales_daily_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_product_sales_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_product_sales`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_product_sales_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_traffic_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_traffic`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_traffic_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_traffic_keyword_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_traffic_keyword`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_traffic_keyword_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_members_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_members`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_members_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_orders_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_orders_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_order_items_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_items_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_order_attribution_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_attribution`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_order_attribution_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_ad_sales_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_ad_sales`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_ad_sales_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_adkeyword_sales_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_adkeyword_sales`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_adkeyword_sales_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_keyword_sales_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_keyword_sales`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_keyword_sales_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_domain_sales_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_domain_sales`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_domain_sales_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_ad_effect_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_ad_effect`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_ad_effect_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');

CREATE OR REPLACE VIEW `rf-ads-db-500505.cafe24.rf_cafe24_sales_pervisitor_current` AS
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_sales_pervisitor`     WHERE report_date <  CURRENT_DATE('Asia/Seoul')
UNION ALL
SELECT * FROM `rf-ads-db-500505.cafe24.rf_cafe24_sales_pervisitor_d0`  WHERE report_date =  CURRENT_DATE('Asia/Seoul');
