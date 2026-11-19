from django.db import migrations, models


def reject_existing_fiat_records(apps, schema_editor):
    alias = schema_editor.connection.alias
    Purchase = apps.get_model("payments", "Purchase")
    RefundRequest = apps.get_model("payments", "RefundRequest")
    if (
        Purchase.objects.using(alias).filter(payment_method="fiat").exists()
        or Purchase.objects.using(alias).filter(amount_fiat__isnull=False).exists()
        or RefundRequest.objects.using(alias).filter(amount_fiat__isnull=False).exists()
    ):
        raise RuntimeError(
            "Crypto-only migration found fiat records. Archive or reconcile them "
            "before removing the legacy fiat fields."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_remove_purchase_purchase_amount_matches_method_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="purchase",
            name="purchase_amount_matches_method",
        ),
        migrations.RemoveConstraint(
            model_name="refundrequest",
            name="refund_amount_positive",
        ),
        migrations.RunPython(
            reject_existing_fiat_records,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="purchase",
            name="amount_fiat",
        ),
        migrations.RemoveField(
            model_name="purchase",
            name="payment_provider",
        ),
        migrations.RemoveField(
            model_name="purchase",
            name="provider_reference",
        ),
        migrations.RemoveField(
            model_name="refundrequest",
            name="amount_fiat",
        ),
        migrations.AlterField(
            model_name="purchase",
            name="amount_lamports",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.AlterField(
            model_name="purchase",
            name="payment_method",
            field=models.CharField(
                choices=[("crypto", "Cryptocurrency")],
                max_length=8,
            ),
        ),
        migrations.AlterField(
            model_name="refundrequest",
            name="amount_lamports",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.AddConstraint(
            model_name="purchase",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_lamports__gt", 0)),
                name="purchase_amount_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="refundrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_lamports__gt", 0)),
                name="refund_amount_positive",
            ),
        ),
    ]
