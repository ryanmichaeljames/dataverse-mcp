using System;
using Microsoft.Xrm.Sdk;

namespace MyOrg.Plugins
{
    /// <summary>
    /// Minimal no-harm sample plug-in for testing Dataverse plug-in registration tools.
    ///
    /// Behavior: writes exactly one trace line via ITracingService and mutates nothing.
    /// The only observable effect is a plugintracelog row, which the live-validation plan
    /// queries to confirm execution (step 9 in issue #76).
    ///
    /// Recommended registration:
    ///   Message:  Create
    ///   Entity:   contact
    ///   Stage:    40 (Post-operation)
    ///   Mode:     0 (Synchronous)
    ///   Typename: MyOrg.Plugins.ContactPlugin
    /// </summary>
    public class ContactPlugin : IPlugin
    {
        /// <summary>
        /// IPlugin entry point.  Obtains ITracingService and writes one trace line.
        /// </summary>
        /// <param name="serviceProvider">
        /// The service provider supplied by the Dataverse sandbox execution environment.
        /// </param>
        public void Execute(IServiceProvider serviceProvider)
        {
            if (serviceProvider == null)
                throw new ArgumentNullException("serviceProvider");

            ITracingService tracingService =
                (ITracingService)serviceProvider.GetService(typeof(ITracingService));

            tracingService.Trace("ContactPlugin fired for contact at {0}", DateTime.UtcNow);
        }
    }
}
