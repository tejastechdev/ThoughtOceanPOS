{
    "name": "ThoughtOcean Restaurant POS",
    "summary": "Restaurant point-of-sale customisations for ThoughtOcean (Australia), incl. Uber Eats order intake",
    "description": """
ThoughtOcean Restaurant POS
===========================
Restaurant point-of-sale customisations built on Odoo 18 Community's POS,
restaurant features and the Australian localisation (GST).

Features
--------

* Uber Eats integration: orders placed on Uber Eats arrive as POS orders in
  the open session (items mapped to your products, paid with an "Uber Eats"
  payment method), are accepted on Uber automatically and show up on the POS
  screen.
""",
    "version": "18.0.1.1.0",
    "category": "Sales/Point of Sale",
    "author": "ThoughtOcean",
    "website": "https://github.com/tejastechdev/ThoughtOceanPOS",
    # Odoo only accepts its own license labels here; the code is MIT (see repository LICENSE).
    "license": "Other OSI approved licence",
    "depends": [
        "point_of_sale",
        "pos_restaurant",
        "l10n_au",
    ],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/ir.model.access.csv",
        "data/res_partner.xml",
        "data/ir_cron.xml",
        "views/uber_eats_order_views.xml",
        "views/res_config_settings_views.xml",
        "views/product_template_views.xml",
        "views/pos_order_views.xml",
    ],
    "assets": {},
    "installable": True,
    "application": False,
    "auto_install": False,
}
