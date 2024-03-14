# Generated by Django 4.2.7 on 2024-03-08 11:55

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('onlinebanking', '0012_transactioncategory_weight'),
    ]

    operations = [
        migrations.CreateModel(
            name='BankingProducts',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('product_name', models.CharField(max_length=56)),
                ('description', models.CharField(max_length=512)),
                ('account_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='onlinebanking.bankaccounttype')),
            ],
        ),
    ]
