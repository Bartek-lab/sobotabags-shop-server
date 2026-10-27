"""Resend template IDs, provisioned once via scripts/provision_resend_templates.py
(re-running that script updates these templates in place -- the IDs stay
stable across re-runs since it looks up by name first)."""

ORDER_CONFIRMATION = "547d33d1-9846-4aed-b89f-d0b99ee122bf"
SHIPMENT_CONFIRMATION = "15d01b00-7a08-4667-84ba-ecad23e7ba33"
GIFT_CARD_ISSUED = "cd374c4f-c2fa-41e8-9360-1594b17c5321"
CONTACT_FORM_ACK = "33b84b31-d080-441c-a3d5-f5efb9a198eb"
CONTACT_FORM_NOTIFY = "70ab77fc-3f98-46d6-9a14-b035899cf675"
NEW_ORDER_ALERT = "6d279b86-e14e-4dc8-bda2-ddb4ce5a2ea6"
LOW_STOCK_ALERT = "1b0e1e0b-0c7d-4029-8104-c7508d6cbfca"
