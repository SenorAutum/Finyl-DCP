# Finyl on AWS: step-by-step (WP9)

This guide puts a complete **staging** copy of Finyl on AWS in Cape Town
(`af-south-1`). Staging means test money only: M-Pesa **sandbox**, demo data,
nothing real. Production later uses the same steps with different settings
(Part 12).

You do most of it by clicking in the AWS website and GitHub. Where you must type
something, it is in a grey box: copy it exactly.

**What you get:** a web address like `https://d1a2b3c4.cloudfront.net` that
opens Finyl, with:

- the API running on **ECS Fargate**: 1 copy for staging, 2+ for production;
- **PostgreSQL 17 on RDS**, private, encrypted, with automatic backups;
- shared encrypted storage (**EFS**) for uploaded ID photos and documents;
- the web app on **S3 + CloudFront**, with HTTPS, no domain needed;
- **generated secrets** (database password, sign-in key, encryption key), which nobody has to type;
- **daily backups** of the database and documents, kept 35 days;
- **alarm e-mails** if the site is down, errors pile up, or a background job fails;
- **one-click deploys** from GitHub, with no AWS keys stored in GitHub.

**Cost:** about **USD 90–110 a month** for staging while it is running. Part 11
shows how to switch it off or remove it.

**Time:** about 1½ hours the first time. Most of that is waiting.

---

## Part 0. Before you start (once)

1. **Have WP8 and WP9 merged into `main` on GitHub.** The deploy button only
   exists once WP9 is on `main`.
2. **Sign in to AWS** at https://console.aws.amazon.com with a user that has
   administrator rights.
3. **Turn on the Cape Town region.** New accounts have it switched off.
   - Click your account name (top right), then **Account**.
   - Scroll to **AWS Regions** and find **Africa (Cape Town) af-south-1**.
   - If it says *Disabled*, click it, then **Enable**. This can take a few minutes.
4. **Switch to Cape Town.** Use the region menu at the top right, next to your
   name: choose **Africa (Cape Town) af-south-1**. Check this every time you
   open AWS. Everything in this guide lives there.
5. **Clean up the old attempt.** The earlier GitHub workflow "Build AWS Database
   and Network" may have created a database that is still costing money.
   - Search for **RDS** in the top search bar and open it.
   - Click **Databases**.
   - If you see a database you did not create on purpose, select it, choose
     **Actions → Delete**, and send me a screenshot first if you are unsure.
6. **Find your 12-digit AWS account number.** Click your name at the top right;
   it is shown as *Account ID*. Keep it handy.

---

## Part 1. Create the environment (the "stack")

A *stack* is everything in the list above, created in one go from one file:
`deploy/aws/finyl-stack.yaml`.

1. **Download the file.**
   - On GitHub, open the repository.
   - Open the folder `deploy`, then `aws`, then click `finyl-stack.yaml`.
   - Click the **Download raw file** button (the arrow pointing down, top right
     of the file).
2. **Open CloudFormation.** In AWS, type **CloudFormation** in the search bar
   and open it. Check that the region is still Cape Town.
3. Click **Create stack**, then **With new resources (standard)**.
4. Choose **Choose an existing template**, then **Upload a template file**, then
   **Choose file**. Pick the `finyl-stack.yaml` you downloaded. Click **Next**.
5. **Stack name:** type exactly:
   ```
   finyl-staging
   ```
   It must be `finyl-` followed by the *Environment name* below.
6. **Parameters.** Leave everything as it is, except:

   | Setting | What to put |
   |---|---|
   | Environment name | `staging` |
   | Run the API now? | `No` (the app image does not exist yet) |
   | AlertEmail | your e-mail address |
   | Create the GitHub sign-in link? | `Yes`. If AWS later says it *already exists*, see Troubleshooting. |
   | GitHubRepo | `SenorAutum/Finyl-DCP` (already filled) |

   Leave **MailFrom**, **CustomDomain** and **CloudFrontCertificateArn** empty
   for now.
7. Click **Next**. On the options page change nothing and click **Next** again.
8. At the bottom, tick **I acknowledge that AWS CloudFormation might create IAM
   resources with custom names**, then click **Submit**.
9. **Wait 20–35 minutes.** The status changes from `CREATE_IN_PROGRESS` to
   `CREATE_COMPLETE`. The database and CloudFront are the slow parts. You can
   close the page and come back.
   - If the status ends in `ROLLBACK_...`, open the **Events** tab, scroll to
     the *first* line with a red *CREATE_FAILED*, and send me a screenshot.
10. **Confirm the alarm e-mail.** AWS sends an e-mail titled *AWS Notification -
    Subscription Confirmation*. Click **Confirm subscription** in it. Without
    this, you get no alarm e-mails.

---

## Part 2. Let GitHub deploy to AWS (once)

The stack created a role that only GitHub's `main` branch of your repository
can use. GitHub only needs to know your account number.

1. On GitHub, open the repository, then **Settings** (top menu).
2. In the left menu: **Secrets and variables → Actions**.
3. Click the **Variables** tab (not *Secrets*), then **New repository variable**.
4. Name:
   ```
   AWS_ACCOUNT_ID
   ```
   Value: your 12-digit account number, with no spaces or dashes. Click **Add variable**.

---

## Part 3. First deploy

1. On GitHub, click **Actions** (top menu).
2. On the left, click **Deploy to AWS**.
3. Click the **Run workflow** button on the right.
   - **Branch:** `main`
   - **Environment name:** `staging`
   - **Load demo data:** tick it, for the first deploy only.
4. Click the green **Run workflow** button. Refresh the page and click the new
   run to watch it. It takes about 10–15 minutes and:
   - builds the API;
   - creates all database tables (it shows `Done: applied 37`);
   - loads the demo DCPs, clients and loans;
   - uploads the web app.
5. It ends green with a yellow warning: *The API is switched off*. That is
   expected; the next part switches it on.

---

## Part 4. Switch the API on

1. In AWS, open **CloudFormation**, click **finyl-staging**, then **Update**
   (top right). If asked, choose **Update stack**.
2. Choose **Use existing template**, then **Next**.
3. Change **Run the API now?** to `Yes`. Change nothing else. Click **Next**,
   then **Next**.
4. Tick the acknowledgement box at the bottom and click **Submit**.
5. Wait about 5 minutes for `UPDATE_COMPLETE`.

---

## Part 5. Open Finyl

1. In CloudFormation, click **finyl-staging**, then the **Outputs** tab.
2. **AppUrl** is your Finyl address. Click it.
3. **The demo password:**
   - In AWS, search for **Secrets Manager** and open it.
   - Click `finyl-staging/demo-password`.
   - Click **Retrieve secret value** and copy the value.
4. **Sign in** with the demo password and one of these users. Each one must
   choose a new password at first sign-in.

   | User | Role |
   |---|---|
   | `superadmin@finyl.app` | Finyl super admin |
   | `admin@mularcredit.co.ke` | DCP administrator (Mular) |
   | `officer@mularcredit.co.ke` | Loan officer (Mular) |
   | `agent@mularcredit.co.ke` | Call centre agent (Mular) |

5. **Check it worked:** sign in as the super admin, open **Super Admin**, and
   scroll to **Background jobs**. Runs start appearing within about 5 minutes.

---

## Part 6. M-Pesa sandbox (test money)

1. Get sandbox keys at https://developer.safaricom.co.ke: sign in, then **My Apps**.
   Create an app with *M-Pesa Sandbox* ticked. Use its **Consumer Key** and
   **Consumer Secret**, test shortcode `600xxx`, and the sandbox passkey from
   the **APIs → M-Pesa Express → Simulate** page.
2. In Finyl, as the DCP administrator, open **DCP Configuration → M-Pesa /
   Daraja**. Choose **sandbox**, enter the keys, and click **Save**.
3. As the Finyl super admin, open **Integrations**. At the bottom of the first
   tab, choose the DCP and click **Register paybill address**. It should answer
   *Registered … Success*.
   - Safaricom sends paybill payments to Finyl only after this step.
   - Repeat it per DCP, and again whenever Finyl's web address changes.
4. The full list of sandbox tests (payout, STK prompt, paybill, reversal) is in
   `deploy/DARAJA_GO_LIVE.md`.

M-Pesa callbacks now use addresses with `/hooks/` in them, because Safaricom
refuses addresses containing the word "mpesa". The old addresses still work.

---

## Part 7. SMS (Uwazii test account) and other optional keys

Optional settings live in one place: the secret `finyl-staging/app-settings`.

1. In **Secrets Manager**, click `finyl-staging/app-settings`.
2. Click **Retrieve secret value**, then **Edit**, then the **Plaintext** tab.
3. Replace `{}` with your settings, for example:
   ```
   {
     "UWAZII_USERNAME": "your-uwazii-user",
     "UWAZII_PASSWORD": "your-uwazii-password",
     "UWAZII_SENDER_ID": "FINYL"
   }
   ```
   Keep the quotes, the colons and the commas between lines. Click **Save**.
4. **Restart the API so it reads the change.** Either run **Deploy to AWS**
   again (without demo data), or:
   - in AWS search **ECS**, then **Clusters**, then **finyl-staging**;
   - click **finyl-staging-api**, then **Update service**;
   - tick **Force new deployment**, then click **Update**.

Any setting from `backend/.env.example` can go in this JSON in the same way.

---

## Part 8. E-mail (sign-in codes and scheduled reports)

E-mail goes out through **Amazon SES**. You do not create a password; the API
has permission to send.

1. In AWS, search **Amazon Simple Email Service** and open it (region Cape Town).
2. Click **Identities**, then **Create identity**, then **Email address**.
   Enter the sender address, e.g. `no-reply@yourdomain.co.ke` or your own
   Gmail for testing. Click **Create identity**.
3. Open the e-mail AWS sends to that address and click the link.
4. **Sandbox limit.** A new SES account can only send *to* verified addresses.
   - For testing, verify each recipient the same way.
   - Before real use, click **Get set up** (left menu) and **Request production
     access**.
5. Put the sender into the stack:
   - CloudFormation → **finyl-staging** → **Update** → **Use existing template**.
   - Set **MailFrom**, e.g. `Finyl <no-reply@yourdomain.co.ke>`.
   - Click **Next**, **Next**, tick the box, **Submit**.

---

## Part 9. Every later update

After a pull request is merged into `main`:

1. Go to GitHub → **Actions** → **Deploy to AWS** → **Run workflow**.
2. Choose environment `staging`, leave **Load demo data** unticked, and click
   **Run workflow**.

The workflow:

- applies any new database changes first;
- starts the new version next to the old one;
- switches over only when the new version is healthy.

If the new version does not become healthy, AWS automatically goes back to the
previous one.

---

## Part 10. Backups, and proving they work

Backups are automatic:

- RDS keeps 7 days that can be restored *to any second*.
- AWS Backup takes a full copy of the database and the documents every night
  at 03:00 Kenyan time, kept 35 days.

**Restore drill.** Do this monthly, and once now after the first night. It is
safe: it never touches the live database.

1. In AWS, click the **CloudShell** icon (a `>_` symbol in the top bar). A
   terminal opens at the bottom.
2. Click **Actions → Upload file** and upload these three files from the
   repository's `deploy/aws` folder: `restore_drill.sh`, `one_off_task.sh`,
   `load_stack_outputs.sh`. Download each from GitHub the same way as in Part 1.
3. Type:
   ```
   bash restore_drill.sh staging
   ```
4. Wait 20–40 minutes. It ends with **RESTORE DRILL PASSED**, the backup it
   used, and how many minutes it took. Write the result in the table in
   `deploy/DISASTER_RECOVERY.md` (section 8).

---

## Part 11. Pause or remove staging

- **Pause the API** (saves about a third of the cost): update the stack with
  **Run the API now?** = `No`.
- **Remove everything:**
  1. Update the stack with **DbDeletionProtection** = `false`.
  2. In CloudFormation, click **Delete**.
  3. On purpose, AWS keeps these, and you can delete them by hand later:
     - a final database snapshot;
     - the secrets;
     - the documents storage (EFS);
     - the web bucket (S3);
     - the backup vault.

---

## Part 12. Production later (same steps, different answers)

Create a second stack named `finyl-prod`, with:

| Setting | Production value |
|---|---|
| Environment name | `prod` |
| Create the GitHub sign-in link? | `No` (staging already created it) |
| Number of API copies | `2` |
| DbInstanceClass | `db.t4g.small` or larger |
| DbMultiAz | `true` |
| DarajaEnvironment | `production` |
| SafaricomIpEnforce | `enforce` |
| CustomDomain + CloudFrontCertificateArn | your domain, with a certificate made in **us-east-1** |

Before the first production deploy:

- **Never tick "Load demo data".** The workflow refuses it for `prod*`.
- **The encryption key.** If live data moves over from the current server, copy
  its `FIELD_ENCRYPTION_KEY` into `finyl-prod/field-encryption-key` *before*
  the first deploy, or national IDs cannot be read. Moving the data itself is a
  separate step; ask before doing it.
- **Between CloudFront and the load balancer**, staging uses plain HTTP inside
  AWS, protected by a secret header. For production with real data, add HTTPS on
  that hop as well; it needs your own domain. That is an open item in the
  change report.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| Stack fails: *Provider with url https://token.actions.githubusercontent.com already exists* | Delete the failed stack. Create it again with **Create the GitHub sign-in link?** = `No`. |
| Stack fails on **Database** with an engine version message | Delete the failed stack. Create it again with **DbEngineVersion** set to a version listed under RDS → **Create database** → PostgreSQL. |
| Workflow fails at **Sign in to AWS** | Check the `AWS_ACCOUNT_ID` variable, and that you ran it from **main**. |
| Workflow fails at **Run database migrations** | Open the step. The lines between `----- output -----` and `------------------` say which file failed. Send me that. |
| Site shows *Forbidden* | Use the **AppUrl** from Outputs, not the load balancer address. |
| Site loads but sign-in fails with *Network error* | The API is off (Part 4), or still starting; wait 3 minutes. |
| No alarm e-mails | Confirm the subscription e-mail (Part 1, step 10). |
| Logs | AWS → **CloudWatch** → **Log groups** → `/finyl/staging/api`. |
