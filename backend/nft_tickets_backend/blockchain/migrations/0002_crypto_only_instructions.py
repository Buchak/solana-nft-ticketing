from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("blockchain", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="chaintransaction",
            name="instruction",
            field=models.CharField(
                choices=[
                    ("initialize_platform", "Initialize platform"),
                    ("create_event", "Create event"),
                    ("add_ticket_type", "Add ticket type"),
                    ("buy_ticket_crypto", "Buy ticket with crypto"),
                    ("consume_ticket", "Consume ticket"),
                    ("cancel_event", "Cancel event"),
                    ("refund_ticket", "Refund ticket"),
                    ("finalize_event", "Finalize event"),
                    ("resolve_frozen_event", "Resolve frozen event"),
                    ("withdraw_funds", "Withdraw funds"),
                ],
                max_length=32,
            ),
        ),
    ]
